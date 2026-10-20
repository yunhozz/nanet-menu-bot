import hashlib
import hmac
import json
import logging
import os
import re
import time
from copy import deepcopy
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import parse_qs
from uuid import uuid4
from zoneinfo import ZoneInfo

import holidays
import requests
from firebase_functions import firestore_fn, https_fn, scheduler_fn
from firebase_functions.options import Timezone
from google.cloud.firestore_v1.services.firestore import FirestoreClient

from nanet_menu.app import run
from nanet_menu.formatter import format_failure_alert_payload
from nanet_menu.retry_store import (
    FIRESTORE_BUDGET,
    RetryIdentity,
    RetryRequest,
    RetryStore,
    RetryStoreError,
    alert_key,
    decode_document,
    validate_retry_message,
)
from nanet_menu.slack import post_message_to_slack, update_message_to_slack

LOGGER = logging.getLogger(__name__)
SEOUL = ZoneInfo("Asia/Seoul")
KOREAN_HOLIDAYS = holidays.KR()
_GITHUB_DISPATCH_URL = (
    "https://api.github.com/repos/yunhozz/nanet-menu-bot/"
    "actions/workflows/daily-menu.yml/dispatches"
)
_RETRY_ACTION_ID = "retry_daily_menu"


def _post_failure_alert(target: date, error: Exception) -> None:
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    if not bot_token or not channel_id:
        LOGGER.error("Slack 실패 알림 전송 실패: Bot Token 또는 채널 ID가 없습니다.")
        return
    LOGGER.info("Slack 실패 알림 전송 시작")
    try:
        post_message_to_slack(
            bot_token,
            channel_id,
            format_failure_alert_payload(
                target,
                str(error),
                retry_date=target,
            ),
        )
    except Exception:
        LOGGER.exception("Slack 실패 알림 전송 실패")
        return
    LOGGER.info("Slack 실패 알림 전송 완료")


def _post_daily_menu(target: date | None = None) -> None:
    delivery_date = target or datetime.now(SEOUL).date()
    holiday_name = KOREAN_HOLIDAYS.get(delivery_date)
    if holiday_name:
        LOGGER.info(
            "공휴일이므로 메뉴 알림을 건너뜁니다: %s (%s)",
            delivery_date.isoformat(),
            holiday_name,
        )
        return
    try:
        run(delivery_date, dry_run=False)
    except Exception as error:
        LOGGER.exception("식단 알림 실행 실패: %s", error)
        _post_failure_alert(delivery_date, error)
        raise


@scheduler_fn.on_schedule(
    schedule="0 10 * * 1-5",
    timezone=Timezone("Asia/Seoul"),
    region="asia-northeast3",
    timeout_sec=300,
    max_instances=1,
    concurrency=1,
    retry_count=0,
    secrets=[
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "NAVER_API_HUB_CLIENT_ID",
        "NAVER_API_HUB_CLIENT_SECRET",
    ],
)
def post_daily_menu(_event: scheduler_fn.ScheduledEvent) -> None:
    """Post the weekday menu at 10:00 Asia/Seoul."""
    _post_daily_menu()


@lru_cache(maxsize=1)
def _get_retry_store() -> RetryStore:
    """Firebase runtime에서만 호출한다. import 시 ADC/client를 초기화하지 않는다."""
    project = os.environ.get("GCLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project or "FIRESTORE_EMULATOR_HOST" in os.environ:
        raise RetryStoreError("명시적 운영 프로젝트가 필요합니다. 로컬 client는 주입하세요.")
    try:
        store = RetryStore(project_id=project)
        store.client = FirestoreClient()
        return store
    except Exception as exc:
        raise RetryStoreError("Firestore runtime 초기화 실패") from exc


def _parse_retry_value(value: str) -> tuple[str, str]:
    generation = "initial"
    if value.startswith("{"):
        decoded = json.loads(value)
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"v", "target_date", "generation"}
            or type(decoded.get("v")) is not int
            or decoded["v"] != 1
        ):
            raise ValueError("지원하지 않는 버튼 세대입니다.")
        value, generation = decoded["target_date"], decoded["generation"]
    if (
        not isinstance(value, str)
        or not isinstance(generation, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", generation)
        or date.fromisoformat(value).isoformat() != value
    ):
        raise ValueError("잘못된 날짜 또는 세대입니다.")
    return value, generation


@https_fn.on_request(
    region="asia-northeast3",
    timeout_sec=10,
    secrets=[
        "SLACK_SIGNING_SECRET",
        "SLACK_CHANNEL_ID",
    ],
)
def retry_daily_menu(request: https_fn.Request) -> https_fn.Response:
    """서명 검증 후 durable handoff를 저장하고 빈 Slack ACK를 반환한다."""
    entered_at = time.monotonic()
    if request.method != "POST":
        return https_fn.Response("Method not allowed", status=405)

    signing_secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not signing_secret:
        return https_fn.Response("Slack signing secret is not configured", status=503)

    raw_body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not timestamp.isdigit() or len(timestamp) > 12:
        return https_fn.Response("Invalid Slack request", status=401)
    signed_at = int(timestamp)
    if not 0 <= int(time.time()) - signed_at <= 300:
        return https_fn.Response("Invalid Slack request", status=401)
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return https_fn.Response("Invalid Slack request", status=401)

    try:
        payload_value = parse_qs(raw_body.decode("utf-8")).get("payload", [""])[0]
        payload = json.loads(payload_value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return https_fn.Response("Invalid Slack payload", status=400)
    if not isinstance(payload, dict):
        return https_fn.Response("Invalid Slack payload", status=400)

    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    channel = payload.get("channel")
    actions = payload.get("actions")
    action = actions[0] if isinstance(actions, list) and actions else None
    target_value = action.get("value") if isinstance(action, dict) else None
    if (
        not channel_id
        or payload.get("type") != "block_actions"
        or not isinstance(channel, dict)
        or channel.get("id") != channel_id
        or not isinstance(action, dict)
        or action.get("action_id") != _RETRY_ACTION_ID
        or not isinstance(target_value, str)
    ):
        return https_fn.Response("Invalid retry action", status=400)
    try:
        target_value, generation = _parse_retry_value(target_value)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValueError("원본 메시지가 없습니다.")
        message_ts = message.get("ts")
        if not isinstance(message_ts, str) or not re.fullmatch(
            r"[0-9]{1,20}\.[0-9]{1,20}", message_ts
        ):
            raise ValueError("잘못된 원본 timestamp입니다.")
        normalized = {key: message[key] for key in ("text", "blocks", "mrkdwn") if key in message}
        validate_retry_message(normalized)
        retry_request = RetryRequest(
            RetryIdentity(channel_id, message_ts, _RETRY_ACTION_ID),
            target_value,
            generation,
            normalized,
        )
    except (ValueError, TypeError):
        return https_fn.Response("Invalid retry message or date", status=400)

    attempt_id = uuid4().hex
    try:
        _get_retry_store().claim_and_enqueue(
            retry_request,
            attempt_id=attempt_id,
            deadline=entered_at + FIRESTORE_BUDGET,
        )
    except Exception:
        # 원본 interaction/secret/SDK 예외 본문은 기록하지 않는다.
        LOGGER.error("재전송 durable handoff 저장 실패 또는 결과 불명. 수동 확인이 필요합니다.")
    return https_fn.Response("", status=200)


_PROGRESS = "식단 재전송 중"
_ACCEPTED = "재전송 요청이 접수되었습니다. 결과는 새 식단 알림으로 확인해 주세요."
_UNKNOWN = (
    "재전송 요청의 접수 여부를 확인하지 못했습니다. "
    "중복 실행을 막기 위해 다시 요청하지 않습니다. 관리자 확인이 필요합니다."
)
_BLOCKED = "재전송을 시작하지 못했습니다. 관리자 확인이 필요합니다."
_REJECTED = "재전송 요청이 거절되었습니다. 원인을 확인한 후 다시 시도해 주세요."
_REJECTED_DISPATCH_CODES: frozenset[int] = frozenset()
_STATUS_BLOCK_ID = "nanet_retry_status"


def _classify_dispatch(status_code: int | None) -> Literal["accepted", "rejected", "unknown"]:
    if status_code in {200, 204}:
        return "accepted"
    if status_code in _REJECTED_DISPATCH_CODES:
        return "rejected"
    return "unknown"


def _validate_outbox(outbox: object) -> None:
    fields = {
        "schema_version",
        "alert_key",
        "attempt_id",
        "generation",
        "target_date",
        "message",
        "created_at",
    }
    if (
        not isinstance(outbox, dict)
        or set(outbox) != fields
        or type(outbox.get("schema_version")) is not int
        or outbox["schema_version"] != 1
        or not isinstance(outbox.get("alert_key"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", outbox["alert_key"])
        or not isinstance(outbox.get("created_at"), datetime)
    ):
        raise ValueError("지원하지 않는 outbox입니다.")
    for field in ("attempt_id", "generation"):
        if not isinstance(outbox[field], str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", outbox[field]
        ):
            raise ValueError("잘못된 outbox 식별자입니다.")
    target = outbox["target_date"]
    if not isinstance(target, str) or date.fromisoformat(target).isoformat() != target:
        raise ValueError("잘못된 outbox 날짜입니다.")
    validate_retry_message(outbox["message"])


def _load_retry_state(store: RetryStore, key: str) -> dict | None:
    path = f"{store.database}/documents/slack_retry_requests/{key}"
    snapshots = list(
        store.client.batch_get_documents(
            request={"database": store.database, "documents": [path]},
            retry=None,
            timeout=FIRESTORE_BUDGET,
        )
    )
    if len(snapshots) != 1:
        raise RetryStoreError("원본 상태 응답 불명")
    snapshot = snapshots[0]
    if snapshot.missing == path:
        return None
    if snapshot.found.name != path:
        raise RetryStoreError("원본 상태 경로 불일치")
    return decode_document(snapshot.found)


def _matches_outbox(state: dict | None, outbox: dict) -> bool:
    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    if (
        not channel_id
        or not isinstance(state, dict)
        or type(state.get("schema_version")) is not int
        or state["schema_version"] != 1
        or state.get("state") != "queued"
        or state.get("channel_id") != channel_id
        or state.get("action_id") != _RETRY_ACTION_ID
    ):
        return False
    if any(
        state.get(field) != outbox[field] for field in ("attempt_id", "generation", "target_date")
    ):
        return False
    timestamp = state.get("message_ts")
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,20}\.[0-9]{1,20}", timestamp):
        return False
    identity = RetryIdentity(state["channel_id"], timestamp, state["action_id"])
    return alert_key(identity) == outbox["alert_key"]


def _message_with_status(
    message: dict, status: str, *, target_date: str, generation: str | None = None
) -> dict:
    result = deepcopy(message)
    blocks = []
    text = result["text"]
    for block in result["blocks"]:
        if block.get("block_id") == _STATUS_BLOCK_ID:
            for element in block.get("elements", []):
                suffix = "\n" + element.get("text", "")
                if text.endswith(suffix):
                    text = text[: -len(suffix)]
            continue
        if block["type"] == "actions":
            elements = [
                element
                for element in block["elements"]
                if element.get("action_id") != _RETRY_ACTION_ID
            ]
            if elements:
                blocks.append({**block, "elements": elements})
        else:
            blocks.append(block)
    blocks.append(
        {
            "type": "context",
            "block_id": _STATUS_BLOCK_ID,
            "elements": [{"type": "plain_text", "text": status}],
        }
    )
    if generation is not None:
        value = json.dumps(
            {"v": 1, "target_date": target_date, "generation": generation}, separators=(",", ":")
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "식단 전송 재시도"},
                        "action_id": _RETRY_ACTION_ID,
                        "value": value,
                        "style": "primary",
                    }
                ],
            }
        )
    return {**result, "text": text + "\n" + status, "blocks": blocks}


def _update_retry_message(
    outbox: dict, state: dict, status: str, *, generation: str | None = None
) -> None:
    update_message_to_slack(
        os.environ["SLACK_BOT_TOKEN"],
        state["channel_id"],
        state["message_ts"],
        _message_with_status(
            outbox["message"], status, target_date=outbox["target_date"], generation=generation
        ),
        timeout=(1.0, 3.0),
        max_attempts=1,
    )


def _process_retry_attempt(outbox: dict[str, Any], *, worker_id: str) -> None:
    try:
        _validate_outbox(outbox)
        store = _get_retry_store()
        state = _load_retry_state(store, outbox["alert_key"])
        if not _matches_outbox(state, outbox):
            return
        owner = store.claim_worker(
            outbox["alert_key"], attempt_id=outbox["attempt_id"], worker_id=worker_id
        )
    except Exception:
        LOGGER.error("재전송 outbox 검증 또는 worker 선점 실패. 후속 작업을 시작하지 않습니다.")
        return
    if owner is None:
        return
    if not os.environ.get("SLACK_BOT_TOKEN") or not os.environ.get("GITHUB_ACTIONS_TOKEN"):
        try:
            changed = store.transition(
                owner, expected_state="preparing", new_state="blocked", reason="missing_credentials"
            )
            if changed and os.environ.get("SLACK_BOT_TOKEN"):
                _update_retry_message(outbox, state, _BLOCKED)
        except Exception:
            LOGGER.error("재전송 인증 누락 상태 기록 또는 표시 실패")
        return
    try:
        _update_retry_message(outbox, state, _PROGRESS)
    except Exception:
        try:
            store.transition(
                owner,
                expected_state="preparing",
                new_state="blocked",
                reason="progress_update_failed",
            )
        except Exception:
            LOGGER.error("진행 표시 실패 후 차단 상태 저장 불명")
        return
    try:
        if not store.transition(owner, expected_state="preparing", new_state="dispatching"):
            return
    except Exception:
        LOGGER.error("dispatching 저장 실패 또는 불명. POST를 시작하지 않습니다.")
        return

    status_code = None
    try:
        response = requests.post(
            _GITHUB_DISPATCH_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {os.environ['GITHUB_ACTIONS_TOKEN']}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "ref": "master",
                "inputs": {"dry_run": "false", "target_date": outbox["target_date"]},
            },
            timeout=(0.5, 1.0),
            allow_redirects=False,
        )
        status_code = response.status_code
    except requests.RequestException:
        LOGGER.error("GitHub 재전송 접수 여부 불명. 자동 재요청하지 않습니다.")
    outcome = _classify_dispatch(status_code)
    if outcome == "rejected":
        generation = uuid4().hex
        try:
            if not store.make_retryable(
                owner, next_generation=generation, dispatch_status_code=status_code
            ):
                return
        except Exception:
            LOGGER.error("재시도 generation 저장 실패 또는 불명. 버튼을 복원하지 않습니다.")
            return
        try:
            _update_retry_message(outbox, state, _REJECTED, generation=generation)
        except Exception:
            try:
                store.block_retryable(owner, generation=generation, reason="restore_update_failed")
            except Exception:
                LOGGER.error("버튼 복원과 조건부 차단 저장 실패. 수동 확인이 필요합니다.")
        return
    try:
        if not store.transition(
            owner,
            expected_state="dispatching",
            new_state=outcome,
            reason=None if outcome == "accepted" else "dispatch_result_unknown",
            dispatch_status_code=status_code,
        ):
            return
    except Exception:
        LOGGER.error("POST 이후 상태 저장 불명. 자동 재요청하지 않습니다.")
        return
    try:
        _update_retry_message(outbox, state, _ACCEPTED if outcome == "accepted" else _UNKNOWN)
    except Exception:
        LOGGER.error("POST 이후 공유 상태 표시 실패. 잠금을 유지합니다.")


@firestore_fn.on_document_created(
    document="slack_retry_outbox/{attempt_id}",
    database="(default)",
    region="asia-northeast3",
    timeout_sec=60,
    secrets=["SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID", "GITHUB_ACTIONS_TOKEN"],
)
def process_retry_outbox(event: firestore_fn.Event) -> None:
    """생성 이벤트를 처리한다. 재전달은 동일 owner를 다시 획득하지 못한다."""
    try:
        project = os.environ.get("GCLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        snapshot = event.data
        if (
            not project
            or event.project != project
            or event.database != "(default)"
            or snapshot is None
            or not snapshot.exists
        ):
            return
        outbox = snapshot.to_dict()
        _validate_outbox(outbox)
        if (
            event.params.get("attempt_id") != outbox["attempt_id"]
            or event.document != f"slack_retry_outbox/{outbox['attempt_id']}"
        ):
            return
    except Exception:
        LOGGER.error("잘못된 재전송 outbox 생성 이벤트")
        return
    _process_retry_attempt(outbox, worker_id=uuid4().hex)
