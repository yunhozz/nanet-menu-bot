"""Firestore 버전 CAS로 재전송을 선점한다. 외부 호출과 자동 쓰기 재시도는 없다."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.types import ArrayValue, Document, MapValue, Value, Write


@dataclass(frozen=True)
class RetryIdentity:
    channel_id: str
    message_ts: str
    action_id: str


@dataclass(frozen=True)
class RetryRequest:
    identity: RetryIdentity
    target_date: str
    generation: str
    message: dict[str, Any]


@dataclass(frozen=True)
class AttemptOwner:
    alert_key: str
    attempt_id: str
    worker_id: str


@dataclass(frozen=True)
class ClaimResult:
    outcome: Literal["claimed", "duplicate", "stale", "blocked"]


class RetryStoreError(Exception):
    """저장 실패 또는 commit 결과 불명. 성공/미접수를 추정하면 안 된다."""


def alert_key(identity: RetryIdentity) -> str:
    value = json.dumps(
        [identity.channel_id, identity.message_ts, identity.action_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


STATES = {"queued", "preparing", "dispatching", "accepted", "retryable", "unknown", "blocked"}
FIRESTORE_BUDGET = 1.5


def encode_value(value: Any) -> Value:
    if value is None:
        return Value(null_value=0)
    if isinstance(value, bool):
        return Value(boolean_value=value)
    if isinstance(value, int):
        return Value(integer_value=value)
    if isinstance(value, str):
        return Value(string_value=value)
    if isinstance(value, datetime):
        return Value(timestamp_value=value)
    if isinstance(value, list):
        return Value(array_value=ArrayValue(values=[encode_value(v) for v in value]))
    if isinstance(value, dict):
        return Value(map_value=MapValue(fields={k: encode_value(v) for k, v in value.items()}))
    raise ValueError("지원하지 않는 저장 값입니다.")


def _decode(value: Value) -> Any:
    # public proto-plus conversion preserves the active oneof without private SDK helpers.
    kind = Value.pb(value).WhichOneof("value_type")
    if kind == "map_value":
        return {k: _decode(v) for k, v in value.map_value.fields.items()}
    if kind == "array_value":
        return [_decode(v) for v in value.array_value.values]
    if kind == "null_value":
        return None
    return getattr(value, kind)


def decode_document(document: Document) -> dict[str, Any]:
    return {k: _decode(v) for k, v in document.fields.items()}


def _identifier(value: str, limit: int = 128) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1," + str(limit) + "}", value
    ):
        raise ValueError("잘못된 식별자입니다.")


class RetryStore:
    def __init__(self, *, project_id: str, client=None):
        if not isinstance(project_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,29}", project_id):
            raise ValueError("명시적인 프로젝트 ID가 필요합니다.")
        self.project_id = project_id
        self.database = f"projects/{project_id}/databases/(default)"
        self.client = client

    def _rpc(self, method, request, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RetryStoreError("Firestore 애플리케이션 예산이 소진됐습니다.")
        return method(request=request, retry=None, timeout=remaining)

    def _name(self, collection, key):
        return f"{self.database}/documents/{collection}/{key}"

    def _compare_and_set(self, key, change, *, deadline, conflict):
        try:
            if self.client is None:
                raise RetryStoreError("Firestore client를 명시적으로 초기화해야 합니다.")
            name = self._name("slack_retry_requests", key)
            responses = list(
                self._rpc(
                    self.client.batch_get_documents,
                    {
                        "database": self.database,
                        "documents": [name],
                    },
                    deadline,
                )
            )
            if len(responses) != 1:
                raise RetryStoreError("원본 문서 조회 결과가 올바르지 않습니다.")
            response = responses[0]
            version = None
            if response.found.name == name:
                if not Document.pb(response.found).HasField("update_time"):
                    raise RetryStoreError("원본 문서 버전이 없습니다.")
                version = response.found.update_time
                current = decode_document(response.found)
            elif response.missing == name:
                current = None
            else:
                raise RetryStoreError("원본 문서 조회 결과가 일치하지 않습니다.")
            result, documents = change(current)
            if not documents:
                return result
            writes = []
            for path, fields, create in documents:
                document = Document(
                    name=path, fields={k: encode_value(v) for k, v in fields.items()}
                )
                write = Write(update=document)
                if create:
                    write.current_document.exists = False
                elif path == name and version is not None:
                    write.current_document.update_time = version
                else:
                    raise RetryStoreError("관찰 버전 없는 문서 쓰기는 금지됩니다.")
                writes.append(write)
            try:
                self._rpc(
                    self.client.commit,
                    {"database": self.database, "writes": writes},
                    deadline,
                )
            except AlreadyExists:
                if any(create for _, _, create in documents):
                    return conflict
                raise
            if deadline <= time.monotonic():
                raise RetryStoreError(
                    "commit 응답이 예산을 초과했습니다. 결과 수동 확인이 필요합니다."
                )
            return result
        except Exception as exc:
            if isinstance(exc, RetryStoreError):
                raise
            raise RetryStoreError("Firestore 저장 실패 또는 commit 결과 불명") from exc

    def claim_and_enqueue(
        self, request: RetryRequest, *, attempt_id: str, deadline: float
    ) -> ClaimResult:
        for value in (
            request.identity.channel_id,
            request.identity.action_id,
            request.generation,
            attempt_id,
        ):
            _identifier(value)
        _identifier(request.identity.message_ts, 64)
        if date.fromisoformat(request.target_date).isoformat() != request.target_date:
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
        validate_retry_message(request.message)
        key = alert_key(request.identity)
        now = datetime.now(UTC)

        def change(current):
            if current is None:
                if request.generation != "initial":
                    return ClaimResult("stale"), []
            else:
                if not self._valid(current) or any(
                    current.get(k) != v
                    for k, v in {
                        "channel_id": request.identity.channel_id,
                        "message_ts": request.identity.message_ts,
                        "action_id": request.identity.action_id,
                        "target_date": request.target_date,
                    }.items()
                ):
                    return ClaimResult("blocked"), []
                if current["generation"] != request.generation:
                    return ClaimResult("stale"), []
                if current["state"] in {"blocked", "unknown"}:
                    return ClaimResult("blocked"), []
                if current["state"] != "retryable":
                    return ClaimResult("duplicate"), []
            state = {
                "schema_version": 1,
                "channel_id": request.identity.channel_id,
                "message_ts": request.identity.message_ts,
                "action_id": request.identity.action_id,
                "target_date": request.target_date,
                "generation": request.generation,
                "attempt_id": attempt_id,
                "worker_id": None,
                "state": "queued",
                "created_at": current["created_at"] if current else now,
                "updated_at": now,
                "reason": None,
                "dispatch_status_code": None,
            }
            outbox = {
                "schema_version": 1,
                "alert_key": key,
                "attempt_id": attempt_id,
                "generation": request.generation,
                "target_date": request.target_date,
                "message": request.message,
                "created_at": now,
            }
            return ClaimResult("claimed"), [
                (self._name("slack_retry_requests", key), state, current is None),
                (self._name("slack_retry_outbox", attempt_id), outbox, True),
            ]

        return self._compare_and_set(
            key, change, deadline=deadline, conflict=ClaimResult("duplicate")
        )

    @staticmethod
    def _valid(current):
        return (
            current.get("schema_version") == 1
            and current.get("state") in STATES
            and isinstance(current.get("generation"), str)
            and isinstance(current.get("attempt_id"), str)
        )

    def claim_worker(
        self, alert_key: str, *, attempt_id: str, worker_id: str
    ) -> AttemptOwner | None:
        for value in (alert_key, attempt_id, worker_id):
            _identifier(value)
        owner = AttemptOwner(alert_key, attempt_id, worker_id)

        def change(current):
            if (
                not current
                or not self._valid(current)
                or current["state"] != "queued"
                or current["attempt_id"] != attempt_id
                or current.get("worker_id") is not None
            ):
                return None, []
            return owner, self._update(alert_key, current, state="preparing", worker_id=worker_id)

        return self._compare_and_set(
            alert_key, change, deadline=time.monotonic() + FIRESTORE_BUDGET, conflict=None
        )

    def _update(self, key, current, **fields):
        return [
            (
                self._name("slack_retry_requests", key),
                {**current, **fields, "updated_at": datetime.now(UTC)},
                False,
            )
        ]

    def _owned(self, current, owner, state):
        return (
            current is not None
            and self._valid(current)
            and current["state"] == state
            and current["attempt_id"] == owner.attempt_id
            and current.get("worker_id") == owner.worker_id
        )

    def transition(
        self,
        owner: AttemptOwner,
        *,
        expected_state: str,
        new_state: str,
        reason: str | None = None,
        dispatch_status_code: int | None = None,
    ) -> bool:
        allowed = {
            ("preparing", "dispatching"),
            ("preparing", "blocked"),
            ("dispatching", "accepted"),
            ("dispatching", "unknown"),
            ("dispatching", "blocked"),
        }
        if (expected_state, new_state) not in allowed:
            return False
        if reason is not None:
            _identifier(reason)
        if dispatch_status_code is not None and (
            type(dispatch_status_code) is not int or not 100 <= dispatch_status_code <= 599
        ):
            raise ValueError("잘못된 HTTP 상태 코드입니다.")

        def change(current):
            if not self._owned(current, owner, expected_state):
                return False, []
            return True, self._update(
                owner.alert_key,
                current,
                state=new_state,
                reason=reason,
                dispatch_status_code=dispatch_status_code,
            )

        return self._compare_and_set(
            owner.alert_key, change, deadline=time.monotonic() + FIRESTORE_BUDGET, conflict=False
        )

    def make_retryable(
        self, owner: AttemptOwner, *, next_generation: str, dispatch_status_code: int
    ) -> bool:
        _identifier(next_generation)
        if (
            type(dispatch_status_code) is not int
            or not 100 <= dispatch_status_code <= 599
            or dispatch_status_code in {200, 204}
        ):
            raise ValueError("접수 상태는 재시도할 수 없습니다.")

        def change(current):
            if not self._owned(current, owner, "dispatching") or next_generation in {
                "initial",
                current["generation"],
            }:
                return False, []
            return True, self._update(
                owner.alert_key,
                current,
                state="retryable",
                generation=next_generation,
                dispatch_status_code=dispatch_status_code,
                reason="dispatch_rejected",
            )

        return self._compare_and_set(
            owner.alert_key, change, deadline=time.monotonic() + FIRESTORE_BUDGET, conflict=False
        )

    def block_retryable(self, owner: AttemptOwner, *, generation: str, reason: str) -> bool:
        _identifier(generation)
        _identifier(reason)

        def change(current):
            if not self._owned(current, owner, "retryable") or current["generation"] != generation:
                return False, []
            return True, self._update(owner.alert_key, current, state="blocked", reason=reason)

        return self._compare_and_set(
            owner.alert_key, change, deadline=time.monotonic() + FIRESTORE_BUDGET, conflict=False
        )


def storage_size(value: Any) -> int:
    """Firestore 문자열·map·array 저장 크기 산식. JSON escape 비용과 무관하다."""
    if isinstance(value, str):
        return len(value.encode("utf-8")) + 1
    if value is None or isinstance(value, bool):
        return 1
    if isinstance(value, (int, datetime)):
        return 8
    if isinstance(value, list):
        return sum(storage_size(item) for item in value)
    if isinstance(value, dict):
        return 32 + sum(storage_size(k) + storage_size(v) for k, v in value.items())
    raise ValueError("지원하지 않는 저장 값입니다.")


# 문서 이름: collection + attempt ID + 16, 각 string은 UTF-8 + 1.
# outbox 메타데이터는 허용한 식별자 최대 길이로 계산한다.
_OUTBOX_FIXED_SIZE = (
    storage_size("slack_retry_outbox")
    + storage_size("a" * 128)
    + 16
    + storage_size(
        {
            "schema_version": 1,
            "alert_key": "a" * 64,
            "attempt_id": "a" * 128,
            "generation": "g" * 128,
            "target_date": "2026-10-20",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )
    + storage_size("message")
)
MAX_MESSAGE_STORAGE_BYTES = 1_048_576 - _OUTBOX_FIXED_SIZE


def validate_retry_message(message: dict[str, Any]) -> None:
    if (
        not isinstance(message, dict)
        or set(message) - {"text", "blocks", "mrkdwn"}
        or not isinstance(message.get("text"), str)
        or not isinstance(message.get("blocks"), list)
        or ("mrkdwn" in message and not isinstance(message["mrkdwn"], bool))
    ):
        raise ValueError("지원하지 않는 실패 알림 schema입니다.")
    if len(message["blocks"]) > 50:
        raise ValueError("Slack blocks 제한 초과")

    def text_object(value):
        if (
            not isinstance(value, dict)
            or set(value) - {"type", "text", "emoji", "verbatim"}
            or value.get("type") not in {"plain_text", "mrkdwn"}
            or not isinstance(value.get("text"), str)
            or any(not isinstance(value[k], bool) for k in ("emoji", "verbatim") if k in value)
        ):
            raise ValueError("지원하지 않는 텍스트 schema입니다.")

    for block in message["blocks"]:
        if not isinstance(block, dict):
            raise ValueError("잘못된 block입니다.")
        kind = block.get("type")
        if kind in {"header", "section"} and not set(block) - {"type", "text", "block_id"}:
            text_object(block.get("text"))
        elif kind == "context" and not set(block) - {"type", "elements", "block_id"}:
            if not isinstance(block.get("elements"), list):
                raise ValueError("잘못된 context입니다.")
            for element in block["elements"]:
                text_object(element)
        elif kind == "actions" and not set(block) - {"type", "elements", "block_id"}:
            if not isinstance(block.get("elements"), list):
                raise ValueError("잘못된 actions입니다.")
            for element in block["elements"]:
                if (
                    not isinstance(element, dict)
                    or element.get("type") != "button"
                    or set(element) - {"type", "text", "action_id", "value", "style", "url"}
                ):
                    raise ValueError("지원하지 않는 버튼입니다.")
                text_object(element.get("text"))
                for field in ("action_id", "value", "style", "url"):
                    if field in element and not isinstance(element[field], str):
                        raise ValueError("잘못된 버튼 필드입니다.")
        else:
            raise ValueError("지원하지 않는 block schema입니다.")
        if "block_id" in block and not isinstance(block["block_id"], str):
            raise ValueError("잘못된 block ID입니다.")
    if storage_size(message) > MAX_MESSAGE_STORAGE_BYTES:
        raise ValueError("Firestore 문서 크기 제한 초과")


def create_emulator_client(endpoint: str | None, *, project_id: str):
    """검증된 loopback에만 연결한다. 누락/오류에서 ADC fallback을 하지 않는다."""
    if not isinstance(endpoint, str):
        raise ValueError("Emulator endpoint가 필요합니다.")
    match = re.fullmatch(r"(127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})", endpoint)
    if (
        not match
        or not 1 <= int(match.group(2)) <= 65535
        or not re.fullmatch(r"demo-[a-z0-9-]{1,25}", project_id)
    ):
        raise ValueError("loopback endpoint와 demo project만 허용됩니다.")
    import grpc
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport

    transport = FirestoreGrpcTransport(
        host=endpoint,
        channel=grpc.insecure_channel(endpoint),
        credentials=AnonymousCredentials(),
    )
    return FirestoreClient(transport=transport)
