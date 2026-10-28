import hashlib
import hmac
import json
import time
from datetime import date
from urllib.parse import urlencode

import pytest
import responses

from nanet_menu import firebase_function
from nanet_menu.errors import MenuParseError


def test_scheduled_function_posts_for_given_date(monkeypatch):
    target = date(2026, 7, 31)
    calls = []
    monkeypatch.setattr(
        firebase_function,
        "run",
        lambda delivery_date, *, dry_run: calls.append((delivery_date, dry_run)),
    )

    firebase_function._post_daily_menu(target)

    assert calls == [(target, False)]


def test_scheduled_function_skips_holiday_without_failure_alert(monkeypatch):
    target = date(2026, 1, 1)

    def fail_if_called(*args, **kwargs):
        pytest.fail("공휴일에는 메뉴 전송이나 실패 알림을 호출하면 안 됩니다.")

    monkeypatch.setattr(firebase_function, "run", fail_if_called)
    monkeypatch.setattr(firebase_function, "_post_failure_alert", fail_if_called)

    firebase_function._post_daily_menu(target)


def test_scheduled_function_reports_failure_and_reraises(monkeypatch):
    target = date(2026, 7, 31)
    error = MenuParseError("식단을 찾지 못했습니다.")
    alerts = []
    monkeypatch.setattr(
        firebase_function,
        "run",
        lambda delivery_date, *, dry_run: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        firebase_function,
        "_post_failure_alert",
        lambda delivery_date, caught: alerts.append((delivery_date, caught)),
    )

    with pytest.raises(MenuParseError, match="식단을 찾지 못했습니다"):
        firebase_function._post_daily_menu(target)

    assert alerts == [(target, error)]


def test_scheduled_function_reports_unexpected_failure_and_reraises(monkeypatch):
    target = date(2026, 7, 31)
    error = RuntimeError("예상하지 못한 오류")
    alerts = []
    monkeypatch.setattr(
        firebase_function,
        "run",
        lambda delivery_date, *, dry_run: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        firebase_function,
        "_post_failure_alert",
        lambda delivery_date, caught: alerts.append((delivery_date, caught)),
    )

    with pytest.raises(RuntimeError, match="예상하지 못한 오류"):
        firebase_function._post_daily_menu(target)

    assert alerts == [(target, error)]


def test_failure_alert_posts_retry_button_to_menu_channel(monkeypatch):
    target = date(2026, 7, 31)
    from nanet_menu.errors import DeliveryError, DeliveryFailure

    error = DeliveryError(
        "식단을 찾지 못했습니다.", failure=DeliveryFailure(stage="prepare", pre_send_confirmed=True)
    )
    sent = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")
    monkeypatch.setattr(
        firebase_function,
        "post_message_to_slack",
        lambda token, channel, payload: sent.append((token, channel, payload)),
    )

    firebase_function._post_failure_alert(target, error)

    assert sent[0][0:2] == ("xoxb-test-token", "C0123456789")
    button = sent[0][2]["blocks"][3]["elements"][0]
    assert button["action_id"] == "retry_daily_menu"
    assert button["value"] == target.isoformat()
    assert "url" not in button


def test_failure_alert_error_does_not_replace_original_failure(monkeypatch):
    target = date(2026, 7, 31)
    original_error = RuntimeError("원래 오류")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")
    monkeypatch.setattr(
        firebase_function,
        "run",
        lambda delivery_date, *, dry_run: (_ for _ in ()).throw(original_error),
    )
    monkeypatch.setattr(
        firebase_function,
        "post_message_to_slack",
        lambda *args: (_ for _ in ()).throw(RuntimeError("알림 생성 오류")),
    )

    with pytest.raises(RuntimeError, match="원래 오류"):
        firebase_function._post_daily_menu(target)


class _FakeRequest:
    def __init__(self, raw_body: bytes, headers: dict[str, str]):
        self._raw_body = raw_body
        self.headers = headers
        self.method = "POST"

    def get_data(self) -> bytes:
        return self._raw_body


def _signed_slack_request(
    payload: dict[str, object],
    secret: str,
    *,
    timestamp: int | None = None,
    signature: str | None = None,
) -> _FakeRequest:
    raw_body = urlencode({"payload": json.dumps(payload, separators=(",", ":"))}).encode()
    signed_at = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(
        secret.encode(),
        b"v0:" + signed_at.encode() + b":" + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return _FakeRequest(
        raw_body,
        {
            "X-Slack-Request-Timestamp": signed_at,
            "X-Slack-Signature": signature or f"v0={digest}",
        },
    )


def _retry_payload(
    *,
    channel_id: str = "C0123456789",
    action_id: str = "retry_daily_menu",
    target_date: str = "2026-07-31",
) -> dict[str, object]:
    return {
        "type": "block_actions",
        "channel": {"id": channel_id},
        "actions": [{"action_id": action_id, "value": target_date}],
        "message": {
            "ts": "1234567890.123456",
            "text": "식단 수집 실패",
            "blocks": [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "재시도"},
                            "action_id": "retry_daily_menu",
                            "value": target_date,
                        }
                    ],
                }
            ],
        },
    }


def _set_retry_secrets(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-signing-secret")
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "github-actions-token")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")


def test_retry_rejects_an_invalid_slack_signature(monkeypatch):
    _set_retry_secrets(monkeypatch)
    request = _signed_slack_request(
        _retry_payload(), "slack-signing-secret", signature="v0=invalid"
    )

    response = firebase_function.retry_daily_menu(request)

    assert response.status_code == 401


def test_retry_rejects_a_stale_slack_timestamp(monkeypatch):
    _set_retry_secrets(monkeypatch)
    request = _signed_slack_request(
        _retry_payload(), "slack-signing-secret", timestamp=int(time.time()) - 301
    )

    response = firebase_function.retry_daily_menu(request)

    assert response.status_code == 401


def test_retry_accepts_only_post_requests(monkeypatch):
    _set_retry_secrets(monkeypatch)
    request = _signed_slack_request(_retry_payload(), "slack-signing-secret")
    request.method = "GET"

    response = firebase_function.retry_daily_menu(request)

    assert response.status_code == 405


@pytest.mark.parametrize(
    "payload",
    [
        _retry_payload(channel_id="C9999999999"),
        _retry_payload(action_id="other_action"),
        _retry_payload(target_date="2026-02-30"),
    ],
    ids=["wrong-channel", "wrong-action", "invalid-date"],
)
def test_retry_rejects_unapproved_action_data(monkeypatch, payload):
    _set_retry_secrets(monkeypatch)
    request = _signed_slack_request(payload, "slack-signing-secret")

    response = firebase_function.retry_daily_menu(request)

    assert response.status_code == 400


@responses.activate
def test_signed_request_enqueues_and_returns_empty_ack(monkeypatch):
    from types import SimpleNamespace

    from nanet_menu.retry_store import ClaimResult

    _set_retry_secrets(monkeypatch)
    enqueued = []

    def enqueue(value, **kwargs):
        enqueued.append((value, kwargs))
        return ClaimResult("claimed")

    monkeypatch.setattr(
        firebase_function,
        "_get_retry_store",
        lambda: SimpleNamespace(claim_and_enqueue=enqueue),
        raising=False,
    )
    response = firebase_function.retry_daily_menu(
        _signed_slack_request(_retry_payload(), "slack-signing-secret")
    )
    assert response.status_code == 200
    assert response.get_data() == b""
    assert len(enqueued) == 1
    assert enqueued[0][0].generation == "initial"
    assert enqueued[0][0].identity.message_ts == "1234567890.123456"
    assert len(responses.calls) == 0


@pytest.mark.parametrize(
    "outcome", ["duplicate", "stale", "blocked", "storage_error", "commit_unknown"]
)
@responses.activate
def test_valid_retry_paths_ack_without_external_calls(monkeypatch, outcome):
    from types import SimpleNamespace

    from nanet_menu.retry_store import ClaimResult, RetryStoreError

    _set_retry_secrets(monkeypatch)

    def enqueue(*args, **kwargs):
        if outcome in {"storage_error", "commit_unknown"}:
            raise RetryStoreError(outcome)
        return ClaimResult(outcome)

    monkeypatch.setattr(
        firebase_function,
        "_get_retry_store",
        lambda: SimpleNamespace(claim_and_enqueue=enqueue),
        raising=False,
    )
    response = firebase_function.retry_daily_menu(
        _signed_slack_request(_retry_payload(), "slack-signing-secret")
    )
    assert response.status_code == 200 and response.get_data() == b""
    assert len(responses.calls) == 0


def test_generation_payload_is_normalized_and_deadline_starts_at_handler_entry(monkeypatch):
    from types import SimpleNamespace

    from nanet_menu.retry_store import ClaimResult

    _set_retry_secrets(monkeypatch)
    clock = [10.0]
    captured = []
    monkeypatch.setattr(firebase_function.time, "monotonic", lambda: clock[0])

    def enqueue(value, **kwargs):
        captured.append((value, kwargs))
        return ClaimResult("claimed")

    def initialize():
        clock[0] += 0.4
        return SimpleNamespace(claim_and_enqueue=enqueue)

    monkeypatch.setattr(firebase_function, "_get_retry_store", initialize, raising=False)
    payload = _retry_payload()
    payload["actions"][0]["value"] = json.dumps(
        {"v": 1, "target_date": "2026-07-31", "generation": "g2"}
    )
    payload["message"]["response_url"] = "secret-response-url"
    payload["message"]["bot_id"] = "B123"
    payload["response_url"] = "secret-response-url"
    response = firebase_function.retry_daily_menu(
        _signed_slack_request(payload, "slack-signing-secret")
    )
    assert response.get_data() == b""
    assert captured[0][0].generation == "g2"
    assert captured[0][1]["deadline"] == 11.5
    assert set(captured[0][0].message) == {"text", "blocks"}


@pytest.mark.parametrize(
    "value",
    [
        '{"v":2,"target_date":"2026-07-31","generation":"g2"}',
        '{"v":1,"target_date":"2026-07-31","generation":""}',
        '{"v":true,"target_date":"2026-07-31","generation":"g2"}',
        '{"v":1,"target_date":"2026-02-30","generation":"g2"}',
    ],
)
def test_invalid_generation_is_rejected_before_store(monkeypatch, value):
    _set_retry_secrets(monkeypatch)
    payload = _retry_payload()
    payload["actions"][0]["value"] = value
    monkeypatch.setattr(
        firebase_function,
        "_get_retry_store",
        lambda: pytest.fail("잘못된 요청에 저장소 초기화 금지"),
        raising=False,
    )
    assert (
        firebase_function.retry_daily_menu(
            _signed_slack_request(payload, "slack-signing-secret")
        ).status_code
        == 400
    )


def test_runtime_store_is_lazy_cached_and_requires_explicit_project(monkeypatch):
    created = []
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(
        firebase_function,
        "FirestoreClient",
        lambda: created.append(object()) or created[-1],
        raising=False,
    )
    firebase_function._get_retry_store.cache_clear()
    from nanet_menu.retry_store import RetryStoreError

    with pytest.raises(RetryStoreError):
        firebase_function._get_retry_store()
    assert not created
    monkeypatch.setenv("GCLOUD_PROJECT", "demo-retry")
    first = firebase_function._get_retry_store()
    assert firebase_function._get_retry_store() is first
    assert first.database == "projects/demo-retry/databases/(default)"
    assert len(created) == 1
    firebase_function._get_retry_store.cache_clear()


@pytest.fixture
def worker_case(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    from nanet_menu.formatter import format_failure_alert_payload
    from nanet_menu.retry_store import AttemptOwner, RetryIdentity, RetryStoreError, alert_key

    _set_retry_secrets(monkeypatch)
    calls = []
    identity = RetryIdentity("C0123456789", "1234567890.123456", "retry_daily_menu")
    key = alert_key(identity)
    message = dict(
        format_failure_alert_payload(
            date(2026, 7, 31), "원래 식단 오류", retry_date=date(2026, 7, 31), retry_allowed=True
        )
    )
    unrelated = {
        "type": "button",
        "text": {"type": "plain_text", "text": "다른 버튼"},
        "action_id": "unrelated",
        "value": "keep",
    }
    message["blocks"][-1]["elements"].append(unrelated)
    from datetime import UTC, datetime

    outbox = {
        "created_at": datetime.now(UTC),
        "schema_version": 1,
        "alert_key": key,
        "attempt_id": "a1",
        "generation": "initial",
        "target_date": "2026-07-31",
        "message": message,
    }

    class Store:
        def __init__(self):
            self.current = {
                "schema_version": 1,
                "channel_id": identity.channel_id,
                "message_ts": identity.message_ts,
                "action_id": identity.action_id,
                "target_date": outbox["target_date"],
                "generation": "initial",
                "attempt_id": "a1",
                "worker_id": None,
                "state": "queued",
            }
            self.failure = None
            self.lost = None

        def claim_worker(self, alert_key, *, attempt_id, worker_id):
            calls.append("claim")
            if self.failure == "claim":
                raise RetryStoreError("unknown claim")
            if self.current["state"] != "queued" or self.current["attempt_id"] != attempt_id:
                return None
            self.current.update(state="preparing", worker_id=worker_id)
            return AttemptOwner(alert_key, attempt_id, worker_id)

        def transition(self, owner, *, expected_state, new_state, **kwargs):
            calls.append(new_state)
            if self.failure == new_state:
                raise RetryStoreError("unknown transition")
            if self.lost == new_state:
                return False
            if (
                self.current["state"] != expected_state
                or self.current["attempt_id"] != owner.attempt_id
                or self.current["worker_id"] != owner.worker_id
            ):
                return False
            self.current.update(state=new_state, **kwargs)
            return True

        def make_retryable(self, owner, *, next_generation, dispatch_status_code):
            calls.append("retryable_commit")
            if self.failure == "retryable":
                raise RetryStoreError("unknown generation")
            if self.lost == "retryable":
                return False
            if self.current["state"] != "dispatching":
                return False
            self.current.update(
                state="retryable",
                generation=next_generation,
                dispatch_status_code=dispatch_status_code,
            )
            return True

        def block_retryable(self, owner, *, generation, reason):
            calls.append("block_retryable")
            if self.failure == "block_retryable":
                raise RetryStoreError("unknown block")
            if self.current["state"] != "retryable" or self.current["generation"] != generation:
                return False
            self.current.update(state="blocked", reason=reason)
            return True

    db = Store()
    updates = []
    posts = []
    reply = SimpleNamespace(status_code=204)

    def update(token, channel, ts, payload, **kwargs):
        calls.append("slack")
        updates.append(deepcopy(payload))
        assert (
            token == "xoxb-test-token"
            and channel == identity.channel_id
            and ts == identity.message_ts
        )
        assert kwargs["max_attempts"] == 1

    def post(url, **kwargs):
        calls.append("post")
        posts.append(kwargs)
        assert kwargs["allow_redirects"] is False
        return reply

    real_reader = getattr(firebase_function, "_load_retry_state", None)
    monkeypatch.setattr(firebase_function, "_get_retry_store", lambda: db, raising=False)
    monkeypatch.setattr(
        firebase_function,
        "_load_retry_state",
        lambda store, key: deepcopy(db.current),
        raising=False,
    )
    monkeypatch.setattr(firebase_function, "update_message_to_slack", update)
    monkeypatch.setattr(firebase_function.requests, "post", post)
    return SimpleNamespace(
        db=db,
        calls=calls,
        updates=updates,
        posts=posts,
        outbox=outbox,
        unrelated=unrelated,
        reply=reply,
        real_reader=real_reader,
    )


@pytest.mark.parametrize("status_code", [200, 204])
def test_duplicate_worker_delivery_dispatches_once_in_correct_order(worker_case, status_code):
    case = worker_case
    case.reply.status_code = status_code
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    firebase_function._process_retry_attempt(case.outbox, worker_id="w2")
    assert len(case.posts) == 1
    assert case.calls[:6] == ["claim", "slack", "dispatching", "post", "accepted", "slack"]
    assert case.db.current["state"] == "accepted"
    assert case.posts[0]["json"] == {
        "ref": "master",
        "inputs": {"dry_run": "false", "target_date": "2026-07-31"},
    }
    assert case.posts[0]["headers"]["Authorization"] == "Bearer github-actions-token"
    progress = case.updates[0]
    assert "원래 식단 오류" in json.dumps(progress, ensure_ascii=False)
    assert "식단 재전송 중" in json.dumps(progress, ensure_ascii=False)
    controls = [
        element
        for block in progress["blocks"]
        if block["type"] == "actions"
        for element in block["elements"]
    ]
    assert controls == [case.unrelated]
    assert "disabled" not in json.dumps(progress)


@pytest.mark.parametrize("failure", ["claim", "progress", "dispatching"])
def test_worker_failure_before_dispatch_never_posts(worker_case, monkeypatch, failure):
    case = worker_case
    if failure == "progress":
        monkeypatch.setattr(
            firebase_function,
            "update_message_to_slack",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("display failed")),
        )
    else:
        case.db.failure = failure
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    assert not case.posts
    if failure == "claim":
        assert not case.updates
    elif failure == "progress":
        assert case.db.current["state"] == "blocked"


def test_lost_dispatch_cas_never_posts_or_publishes_more(worker_case):
    worker_case.db.lost = "dispatching"
    firebase_function._process_retry_attempt(worker_case.outbox, worker_id="w1")
    assert not worker_case.posts
    assert len(worker_case.updates) == 1


@pytest.mark.parametrize(
    "status, expected",
    [
        (200, "accepted"),
        (204, "accepted"),
        (201, "unknown"),
        (202, "unknown"),
        (301, "unknown"),
        (307, "unknown"),
        (400, "unknown"),
        (401, "unknown"),
        (403, "unknown"),
        (404, "unknown"),
        (408, "unknown"),
        (422, "unknown"),
        (429, "unknown"),
        (500, "unknown"),
        (503, "unknown"),
        (None, "unknown"),
    ],
)
def test_dispatch_classifier_has_no_unverified_rejection(status, expected):
    assert firebase_function._classify_dispatch(status) == expected


@pytest.mark.parametrize("status", [201, 301, 403, 408, 422, 500])
def test_unknown_status_keeps_lock_and_does_not_restore(worker_case, monkeypatch, status):
    from types import SimpleNamespace

    case = worker_case

    def post(*args, **kwargs):
        case.posts.append(kwargs)
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr(firebase_function.requests, "post", post)
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    firebase_function._process_retry_attempt(case.outbox, worker_id="w2")
    assert len(case.posts) == 1
    assert case.db.current["state"] == "unknown"
    assert "retryable_commit" not in case.calls
    assert "관리자 확인" in json.dumps(case.updates[-1], ensure_ascii=False)


@pytest.mark.parametrize("failure", ["accepted_store", "accepted_display", "network"])
def test_post_result_failure_does_not_redispatch(worker_case, monkeypatch, failure):
    import requests

    case = worker_case
    if failure == "accepted_store":
        case.db.failure = "accepted"
    elif failure == "accepted_display":
        original_update = firebase_function.update_message_to_slack

        def update(*args, **kwargs):
            if case.db.current["state"] == "accepted":
                raise RuntimeError("display failed after accept")
            return original_update(*args, **kwargs)

        monkeypatch.setattr(firebase_function, "update_message_to_slack", update)
    else:

        def post(*args, **kwargs):
            case.posts.append(kwargs)
            raise requests.Timeout("receipt unknown")

        monkeypatch.setattr(firebase_function.requests, "post", post)
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    firebase_function._process_retry_attempt(case.outbox, worker_id="w2")
    assert len(case.posts) == 1
    assert case.db.current["state"] in {"dispatching", "accepted", "unknown"}


def test_rejection_commits_generation_before_button_publish(worker_case, monkeypatch):
    # Synthetic classifier outcome: does not assert any real HTTP code is a confirmed rejection.
    case = worker_case
    monkeypatch.setattr(
        firebase_function, "_classify_dispatch", lambda status: "rejected", raising=False
    )
    case.reply.status_code = 422
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    assert case.calls.index("retryable_commit") < len(case.calls) - 1
    assert case.calls[-1] == "slack"
    assert case.db.current["state"] == "retryable"
    controls = [
        element
        for block in case.updates[-1]["blocks"]
        if block["type"] == "actions"
        for element in block["elements"]
        if element.get("action_id") == "retry_daily_menu"
    ]
    assert len(controls) == 1
    restored = json.loads(controls[0]["value"])
    assert restored == {
        "v": 1,
        "target_date": "2026-07-31",
        "generation": case.db.current["generation"],
    }
    assert restored["generation"] != "initial"


@pytest.mark.parametrize(
    "new_attempt, block_failure", [(False, False), (True, False), (False, True)]
)
def test_failed_restore_conditionally_blocks_only_same_generation(
    worker_case, monkeypatch, new_attempt, block_failure
):
    case = worker_case
    monkeypatch.setattr(
        firebase_function, "_classify_dispatch", lambda status: "rejected", raising=False
    )
    case.reply.status_code = 422
    original_update = firebase_function.update_message_to_slack

    def update(*args, **kwargs):
        if case.db.current["state"] == "retryable":
            if new_attempt:
                case.db.current.update(state="queued", attempt_id="a2", worker_id=None)
            raise RuntimeError("restore receipt unknown")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(firebase_function, "update_message_to_slack", update)
    if block_failure:
        case.db.failure = "block_retryable"
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    assert case.db.current["state"] == (
        "queued" if new_attempt else "retryable" if block_failure else "blocked"
    )
    assert len(case.posts) == 1


@pytest.mark.parametrize("lost", [False, True])
def test_generation_commit_failure_prevents_button_publish(worker_case, monkeypatch, lost):
    case = worker_case
    monkeypatch.setattr(
        firebase_function, "_classify_dispatch", lambda status: "rejected", raising=False
    )
    case.reply.status_code = 422
    if lost:
        case.db.lost = "retryable"
    else:
        case.db.failure = "retryable"
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    assert len(case.updates) == 1
    assert case.db.current["state"] == "dispatching"


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema_version", True),
        ("alert_key", "bad"),
        ("attempt_id", "../bad"),
        ("generation", ""),
        ("target_date", "bad"),
        ("message", {"text": "bad", "blocks": [{"type": "unknown"}]}),
        ("response_url", "secret"),
    ],
)
def test_invalid_outbox_never_claims_or_calls_external(worker_case, field, value):
    case = worker_case
    case.outbox[field] = value
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    assert not case.calls and not case.posts


@pytest.mark.parametrize(
    "field, value",
    [
        ("channel_id", "OTHER"),
        ("message_ts", "1.2"),
        ("action_id", "other"),
        ("attempt_id", "other"),
        ("target_date", "2026-07-30"),
        ("generation", "g2"),
        ("schema_version", 2),
    ],
)
def test_outbox_state_mismatch_never_claims_or_calls_external(worker_case, field, value):
    worker_case.db.current[field] = value
    firebase_function._process_retry_attempt(worker_case.outbox, worker_id="w1")
    assert not worker_case.calls and not worker_case.posts


def test_state_read_uses_public_gapic_without_retry(worker_case):
    from types import SimpleNamespace

    from google.cloud.firestore_v1.types import BatchGetDocumentsResponse, Document

    from nanet_menu.retry_store import encode_value

    case = worker_case
    calls = []
    path = (
        "projects/demo-retry/databases/(default)/documents/slack_retry_requests/"
        + case.outbox["alert_key"]
    )

    def read(**kwargs):
        calls.append(kwargs)
        return iter(
            [
                BatchGetDocumentsResponse(
                    found=Document(
                        name=path, fields={k: encode_value(v) for k, v in case.db.current.items()}
                    )
                )
            ]
        )

    store = SimpleNamespace(
        database="projects/demo-retry/databases/(default)",
        client=SimpleNamespace(batch_get_documents=read),
    )
    # fixture replaces this helper for behavioral worker tests; get the real module function.
    helper = case.real_reader
    assert helper(store, case.outbox["alert_key"]) == case.db.current
    assert calls[0]["request"] == {"database": store.database, "documents": [path]}
    assert calls[0]["retry"] is None and 0 < calls[0]["timeout"] <= 1.5


@pytest.mark.parametrize(
    "mutation", ["none", "missing", "wrong_id", "wrong_database", "wrong_project"]
)
def test_trigger_validates_created_outbox_event(worker_case, monkeypatch, mutation):
    from types import SimpleNamespace

    case = worker_case
    captured = []
    monkeypatch.setenv("GCLOUD_PROJECT", "demo-retry")
    monkeypatch.setattr(
        firebase_function,
        "_process_retry_attempt",
        lambda outbox, **kw: captured.append((outbox, kw)),
        raising=False,
    )
    event = SimpleNamespace(
        data=SimpleNamespace(exists=True, to_dict=lambda: case.outbox),
        params={"attempt_id": "a1"},
        document="slack_retry_outbox/a1",
        database="(default)",
        project="demo-retry",
    )
    if mutation == "missing":
        event.data = None
    elif mutation == "wrong_id":
        event.params["attempt_id"] = "other"
    elif mutation == "wrong_database":
        event.database = "other"
    elif mutation == "wrong_project":
        event.project = "other"
    firebase_function.process_retry_outbox.__wrapped__(event)
    assert len(captured) == (1 if mutation == "none" else 0)


@pytest.mark.parametrize(
    "stage",
    ["claim", "progress", "dispatching", "github_accepted", "generation", "button_published"],
)
def test_crash_boundary_redelivery_never_reposts(worker_case, monkeypatch, stage):
    case = worker_case

    class Crash(BaseException):
        pass

    if stage in {"generation", "button_published"}:
        monkeypatch.setattr(
            firebase_function, "_classify_dispatch", lambda status: "rejected", raising=False
        )
        case.reply.status_code = 422
    if stage == "claim":
        original = case.db.claim_worker

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise Crash()

        monkeypatch.setattr(case.db, "claim_worker", crash)
    elif stage == "dispatching":
        original = case.db.transition

        def crash(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs["new_state"] == "dispatching":
                raise Crash()
            return result

        monkeypatch.setattr(case.db, "transition", crash)
    elif stage == "github_accepted":
        original = firebase_function.requests.post

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise Crash()

        monkeypatch.setattr(firebase_function.requests, "post", crash)
    elif stage == "generation":
        original = case.db.make_retryable

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise Crash()

        monkeypatch.setattr(case.db, "make_retryable", crash)
    else:
        original = firebase_function.update_message_to_slack

        def crash(*args, **kwargs):
            result = original(*args, **kwargs)
            if stage == "progress" or case.db.current["state"] == "retryable":
                raise Crash()
            return result

        monkeypatch.setattr(firebase_function, "update_message_to_slack", crash)
    with pytest.raises(Crash):
        firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    post_count = len(case.posts)
    firebase_function._process_retry_attempt(case.outbox, worker_id="w2")
    assert len(case.posts) == post_count
    assert post_count == (0 if stage in {"claim", "progress", "dispatching"} else 1)


def test_entrypoint_exports_and_secrets_are_split_by_role():
    from pathlib import Path
    from runpy import run_path
    from types import SimpleNamespace

    main = SimpleNamespace(**run_path(str(Path(__file__).resolve().parents[1] / "main.py")))

    assert set(main.__all__) == {"post_daily_menu", "retry_daily_menu", "process_retry_outbox"}

    def secrets(function):
        return {item["key"] for item in function.__firebase_endpoint__.secretEnvironmentVariables}

    assert secrets(firebase_function.retry_daily_menu) == {
        "SLACK_SIGNING_SECRET",
        "SLACK_CHANNEL_ID",
    }
    assert secrets(firebase_function.process_retry_outbox) == {
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "GITHUB_ACTIONS_TOKEN",
    }
    endpoint = firebase_function.process_retry_outbox.__firebase_endpoint__
    assert endpoint.region == ["asia-northeast3"]
    assert endpoint.eventTrigger["eventType"] == "google.cloud.firestore.document.v1.created"
    assert endpoint.eventTrigger["retry"] is False


@pytest.mark.parametrize("failure", ["read_error", "no_owner"])
def test_worker_context_read_or_claim_loss_has_no_effects(worker_case, monkeypatch, failure):
    from nanet_menu.retry_store import RetryStoreError

    if failure == "read_error":
        monkeypatch.setattr(
            firebase_function,
            "_load_retry_state",
            lambda *a: (_ for _ in ()).throw(RetryStoreError("read unknown")),
        )
    else:
        monkeypatch.setattr(worker_case.db, "claim_worker", lambda *a, **kw: None)
    firebase_function._process_retry_attempt(worker_case.outbox, worker_id="w1")
    assert not worker_case.posts and not worker_case.updates


@pytest.mark.parametrize("secret", ["SLACK_BOT_TOKEN", "GITHUB_ACTIONS_TOKEN"])
def test_missing_worker_credentials_block_without_dispatch(worker_case, monkeypatch, secret):
    monkeypatch.delenv(secret)
    firebase_function._process_retry_attempt(worker_case.outbox, worker_id="w1")
    assert worker_case.db.current["state"] == "blocked"
    assert not worker_case.posts


def test_handler_does_not_need_worker_credentials(monkeypatch):
    from types import SimpleNamespace

    from nanet_menu.retry_store import ClaimResult

    _set_retry_secrets(monkeypatch)
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    monkeypatch.delenv("GITHUB_ACTIONS_TOKEN")
    monkeypatch.setattr(
        firebase_function,
        "_get_retry_store",
        lambda: SimpleNamespace(claim_and_enqueue=lambda *a, **kw: ClaimResult("claimed")),
    )
    response = firebase_function.retry_daily_menu(
        _signed_slack_request(_retry_payload(), "slack-signing-secret")
    )
    assert response.status_code == 200 and response.get_data() == b""


@pytest.mark.parametrize(
    "outcome", ["claimed", "duplicate", "stale", "storage_error", "commit_unknown"]
)
def test_local_ack_timing_with_injected_initialization_and_store_delay(monkeypatch, outcome):
    from types import SimpleNamespace

    from nanet_menu.retry_store import ClaimResult, RetryStoreError

    _set_retry_secrets(monkeypatch)
    observations = []

    def enqueue(*args, deadline, **kwargs):
        remaining = deadline - time.monotonic()
        assert 0 < remaining < 1.5
        time.sleep(0.02)
        if outcome in {"storage_error", "commit_unknown"}:
            raise RetryStoreError(outcome)
        return ClaimResult(outcome)

    def initialize():
        time.sleep(0.02)
        return SimpleNamespace(claim_and_enqueue=enqueue)

    monkeypatch.setattr(firebase_function, "_get_retry_store", initialize)
    monkeypatch.setattr(
        firebase_function,
        "_process_retry_attempt",
        lambda *a, **kw: pytest.fail("worker 지연이 ACK 경로에 진입하면 안 됩니다."),
    )
    for _ in range(3):
        signed = _signed_slack_request(_retry_payload(), "slack-signing-secret")
        started = time.monotonic()
        response = firebase_function.retry_daily_menu(signed)
        observations.append(time.monotonic() - started)
        assert response.status_code == 200 and response.get_data() == b""
    assert max(observations) < 2
    print(
        {
            "timing_path": outcome,
            "environment": "Python local / fake initialization+store",
            "repeats": 3,
            "injected_init_seconds": 0.02,
            "injected_store_seconds": 0.02,
            "maximum_seconds": round(max(observations), 6),
        }
    )


def test_local_ack_timing_at_dependency_budget_boundary(monkeypatch):
    from types import SimpleNamespace

    from nanet_menu.retry_store import RetryStoreError

    _set_retry_secrets(monkeypatch)

    def enqueue(*args, deadline, **kwargs):
        remaining = deadline - time.monotonic()
        time.sleep(min(remaining, 1.45))
        raise RetryStoreError("injected deadline expiry")

    monkeypatch.setattr(
        firebase_function, "_get_retry_store", lambda: SimpleNamespace(claim_and_enqueue=enqueue)
    )
    signed = _signed_slack_request(_retry_payload(), "slack-signing-secret")
    started = time.monotonic()
    response = firebase_function.retry_daily_menu(signed)
    elapsed = time.monotonic() - started
    assert response.status_code == 200 and response.get_data() == b""
    assert 1.4 < elapsed < 2
    print(
        {
            "timing_path": "injected_dependency_budget_boundary",
            "repeats": 1,
            "maximum_seconds": round(elapsed, 6),
            "real_ADC_and_platform_cold_start": "not measured",
        }
    )


def test_import_does_not_initialize_adc_or_gapic():
    import subprocess
    import sys

    script = """
import google.auth
from google.cloud.firestore_v1.services.firestore import FirestoreClient

def forbidden(*args, **kwargs):
    raise AssertionError("import initialized ADC/client")
google.auth.default = forbidden
FirestoreClient.__init__ = forbidden
import nanet_menu.firebase_function
import main
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_missing_channel_never_claims_even_with_forged_null_identity(worker_case, monkeypatch):
    from nanet_menu.retry_store import RetryIdentity, alert_key

    case = worker_case
    monkeypatch.delenv("SLACK_CHANNEL_ID")
    case.db.current["channel_id"] = None
    case.outbox["alert_key"] = alert_key(
        RetryIdentity(None, "1234567890.123456", "retry_daily_menu")
    )
    firebase_function._process_retry_attempt(case.outbox, worker_id="w1")
    assert not case.calls and not case.posts and not case.updates


@pytest.mark.firestore_emulator
def test_loopback_handler_handoff_and_duplicate_events(monkeypatch):
    """실제 loopback 저장 계약과 사용자 handler/worker 연결. 외부 API는 대역만 사용한다."""
    import os
    from types import SimpleNamespace
    from uuid import uuid4

    from nanet_menu.retry_store import (
        RetryIdentity,
        RetryStore,
        alert_key,
        create_emulator_client,
        decode_document,
    )

    endpoint = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if endpoint is None:
        pytest.skip("loopback Emulator 미설정: client/ADC 초기화 생략")
    client = create_emulator_client(endpoint, project_id="demo-slack-retry")
    db = RetryStore(project_id="demo-slack-retry", client=client)
    _set_retry_secrets(monkeypatch)
    monkeypatch.setenv("GCLOUD_PROJECT", "demo-slack-retry")
    channel_id = "C" + uuid4().hex
    monkeypatch.setenv("SLACK_CHANNEL_ID", channel_id)
    monkeypatch.setattr(firebase_function, "_get_retry_store", lambda: db)
    posts, updates, measurements = [], [], []
    monkeypatch.setattr(
        firebase_function, "update_message_to_slack", lambda *a, **kw: updates.append((a, kw))
    )

    def post(*args, **kwargs):
        assert kwargs["allow_redirects"] is False
        posts.append(kwargs)
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(firebase_function.requests, "post", post)
    paths = set()

    def read(path):
        snapshots = list(
            client.batch_get_documents(
                request={"database": db.database, "documents": [path]}, retry=None, timeout=5
            )
        )
        return decode_document(snapshots[0].found) if snapshots[0].found.name else None

    class FaultClient:
        def __init__(self, failure):
            self.failure = failure

        def batch_get_documents(self, **kwargs):
            if self.failure == "storage_error":
                raise TimeoutError("injected read failure")
            return client.batch_get_documents(**kwargs)

        def commit(self, **kwargs):
            client.commit(**kwargs)
            raise TimeoutError("injected response loss after applied commit")

    try:
        initial_payload = _retry_payload(channel_id=channel_id)
        payloads = [
            (name, json.loads(json.dumps(initial_payload)))
            for name in ("success", "duplicate", "stale", "storage_error", "commit_unknown")
        ]
        for name, payload in payloads:
            if name == "stale":
                payload["actions"][0]["value"] = json.dumps(
                    {"v": 1, "target_date": "2026-07-31", "generation": "stale-generation"}
                )
            if name in {"storage_error", "commit_unknown"}:
                payload["message"]["ts"] = "1234567890." + (
                    "200000" if name == "storage_error" else "300000"
                )
                db.client = FaultClient(name)
            state_key = alert_key(
                RetryIdentity(channel_id, payload["message"]["ts"], "retry_daily_menu")
            )
            state_path = db._name("slack_retry_requests", state_key)
            paths.add(state_path)
            signed = _signed_slack_request(payload, "slack-signing-secret")
            started = time.monotonic()
            response = firebase_function.retry_daily_menu(signed)
            elapsed = time.monotonic() - started
            db.client = client
            measurements.append((name, elapsed))
            assert response.status_code == 200 and response.get_data() == b""
            assert elapsed < 2, measurements
            assert not posts and not updates
            state = read(state_path)
            if name == "storage_error":
                assert state is None
            else:
                assert state["state"] == "queued"
                outbox_path = db._name("slack_retry_outbox", state["attempt_id"])
                paths.add(outbox_path)
                assert read(outbox_path)["alert_key"] == state_key
                if name == "success":
                    original_state, original_outbox_path = state, outbox_path
        outbox = read(original_outbox_path)
        event = SimpleNamespace(
            data=SimpleNamespace(exists=True, to_dict=lambda: outbox),
            params={"attempt_id": original_state["attempt_id"]},
            document="slack_retry_outbox/" + original_state["attempt_id"],
            database="(default)",
            project="demo-slack-retry",
        )
        for _ in range(20):
            firebase_function.process_retry_outbox.__wrapped__(event)
        assert len(posts) == 1 and len(updates) == 2
        print(
            {
                "loopback_handler_repeats_per_path": 1,
                "maxima_seconds": {name: round(elapsed, 6) for name, elapsed in measurements},
                "client_state": "anonymous GAPIC injected; real ADC/platform cold start excluded",
                "duplicate_events": 20,
                "github_posts": len(posts),
                "slack_updates": len(updates),
            }
        )
    finally:
        db.client = client
        try:
            for path in paths:
                client.delete_document(request={"name": path}, retry=None, timeout=5)
            assert all(read(path) is None for path in paths)
            print({"T2_loopback_cleanup": "verified_missing", "paths": len(paths)})
        finally:
            client.transport.close()


def test_unknown_failure_alert_has_no_retry_button(monkeypatch):
    sent = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "channel")
    monkeypatch.setattr(
        firebase_function, "post_message_to_slack", lambda *args: sent.append(args[-1])
    )
    firebase_function._post_failure_alert(date(2026, 7, 31), MenuParseError("unknown"))
    assert not any(block["type"] == "actions" for block in sent[0]["blocks"])


def test_scheduled_endpoint_preserves_configuration():
    endpoint = firebase_function.post_daily_menu.__firebase_endpoint__
    assert (endpoint.minInstances, endpoint.maxInstances, endpoint.concurrency) == (1, 1, 1)
    assert endpoint.timeoutSeconds == 300
    assert endpoint.scheduleTrigger["schedule"] == "0 10 * * 1-5"
    assert endpoint.scheduleTrigger["timeZone"] == "Asia/Seoul"
    assert endpoint.scheduleTrigger["retryConfig"]["retryCount"] == 0
