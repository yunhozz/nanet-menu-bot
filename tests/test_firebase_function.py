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
    error = MenuParseError("식단을 찾지 못했습니다.")
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
                    "elements": [{"action_id": "retry_daily_menu", "value": target_date}],
                }
            ],
        },
    }


def _set_retry_secrets(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-signing-secret")
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "github-actions-token")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")


@responses.activate
@pytest.mark.parametrize("status_code", [200, 204])
def test_signed_retry_dispatches_original_date_and_removes_the_button(monkeypatch, status_code):
    _set_retry_secrets(monkeypatch)
    responses.post(
        "https://api.github.com/repos/yunhozz/nanet-menu-bot/actions/workflows/daily-menu.yml/dispatches",
        json={"id": 1234, "html_url": "https://github.com/example/actions/runs/1234"}
        if status_code == 200
        else None,
        status=status_code,
    )
    responses.post("https://slack.com/api/chat.update", json={"ok": True}, status=200)
    request = _signed_slack_request(_retry_payload(), "slack-signing-secret")

    response = firebase_function.retry_daily_menu(request)

    body = json.loads(response.get_data(as_text=True))
    dispatch = responses.calls[0].request
    assert response.status_code == 200
    assert body["response_type"] == "ephemeral"
    assert "시작" in body["text"]
    assert "html_url" not in body["text"]
    assert dispatch.headers["Authorization"] == "Bearer github-actions-token"
    assert json.loads(dispatch.body) == {
        "ref": "master",
        "inputs": {"dry_run": "false", "target_date": "2026-07-31"},
    }
    updated = json.loads(responses.calls[1].request.body)
    assert updated["channel"] == "C0123456789"
    assert updated["ts"] == "1234567890.123456"
    assert all(block["type"] != "actions" for block in updated["blocks"])


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
def test_retry_reports_github_dispatch_failure(monkeypatch):
    _set_retry_secrets(monkeypatch)
    responses.post(
        "https://api.github.com/repos/yunhozz/nanet-menu-bot/actions/workflows/daily-menu.yml/dispatches",
        body="forbidden",
        status=403,
    )
    request = _signed_slack_request(_retry_payload(), "slack-signing-secret")

    response = firebase_function.retry_daily_menu(request)

    body = json.loads(response.get_data(as_text=True))
    assert response.status_code == 200
    assert body["response_type"] == "ephemeral"
    assert "실패" in body["text"]


@responses.activate
def test_retry_still_reports_success_if_slack_button_update_fails(monkeypatch):
    _set_retry_secrets(monkeypatch)
    responses.post(
        "https://api.github.com/repos/yunhozz/nanet-menu-bot/actions/workflows/daily-menu.yml/dispatches",
        status=204,
    )
    responses.post("https://slack.com/api/chat.update", json={"ok": False}, status=200)
    request = _signed_slack_request(_retry_payload(), "slack-signing-secret")

    response = firebase_function.retry_daily_menu(request)

    body = json.loads(response.get_data(as_text=True))
    assert response.status_code == 200
    assert "시작" in body["text"]
