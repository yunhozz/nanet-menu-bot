import logging

import pytest
import responses

from nanet_menu.errors import SlackError
from nanet_menu.slack import post_and_pin_to_slack, post_to_slack

WEBHOOK = "https://hooks.slack.test/services/SECRET/VALUE"
BOT_TOKEN = "xoxb-test-token"
CHANNEL_ID = "C0123456789"
PAYLOAD = {"text": "hello", "mrkdwn": False, "blocks": []}


@responses.activate
def test_webhook_success():
    responses.post(WEBHOOK, body="ok", status=200)

    post_to_slack(WEBHOOK, PAYLOAD)

    assert responses.calls[0].request.body == b'{"text": "hello", "mrkdwn": false, "blocks": []}'


@responses.activate
def test_webhook_4xx_is_not_retried():
    responses.post(WEBHOOK, body="invalid_payload", status=400)

    with pytest.raises(SlackError, match="status=400"):
        post_to_slack(WEBHOOK, PAYLOAD)

    assert len(responses.calls) == 1


@responses.activate
def test_webhook_5xx_is_retried(monkeypatch):
    monkeypatch.setattr("nanet_menu.slack.time.sleep", lambda _: None)
    responses.post(WEBHOOK, body="error", status=503)
    responses.post(WEBHOOK, body="error", status=503)
    responses.post(WEBHOOK, body="ok", status=200)

    post_to_slack(WEBHOOK, PAYLOAD)

    assert len(responses.calls) == 3


@responses.activate
def test_webhook_429_honors_retry_after(monkeypatch):
    delays = []
    monkeypatch.setattr("nanet_menu.slack.time.sleep", delays.append)
    responses.post(WEBHOOK, body="rate_limited", status=429, headers={"Retry-After": "2"})
    responses.post(WEBHOOK, body="ok", status=200)

    post_to_slack(WEBHOOK, PAYLOAD)

    assert delays == [2.0]
    assert len(responses.calls) == 2


@responses.activate
def test_webhook_url_is_not_logged(caplog):
    responses.post(WEBHOOK, body="invalid_payload", status=400)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(SlackError):
        post_to_slack(WEBHOOK, PAYLOAD)

    assert WEBHOOK not in caplog.text


@responses.activate
def test_post_and_pin_success():
    responses.post(
        "https://slack.com/api/chat.postMessage",
        json={"ok": True, "channel": CHANNEL_ID, "ts": "1234567890.123456"},
        status=200,
    )
    responses.post(
        "https://slack.com/api/pins.add",
        json={"ok": True},
        status=200,
    )

    post_and_pin_to_slack(BOT_TOKEN, CHANNEL_ID, PAYLOAD)

    assert len(responses.calls) == 2
    assert responses.calls[0].request.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert responses.calls[0].request.body == (
        b'{"channel": "C0123456789", "text": "hello", "mrkdwn": false, "blocks": []}'
    )
    assert responses.calls[1].request.body == (
        b'{"channel": "C0123456789", "timestamp": "1234567890.123456"}'
    )


@responses.activate
def test_post_failure_does_not_try_to_pin():
    responses.post(
        "https://slack.com/api/chat.postMessage",
        json={"ok": False, "error": "not_in_channel"},
        status=200,
    )

    with pytest.raises(SlackError, match="chat.postMessage.*not_in_channel"):
        post_and_pin_to_slack(BOT_TOKEN, CHANNEL_ID, PAYLOAD)

    assert len(responses.calls) == 1


@responses.activate
def test_pin_failure_is_reported():
    responses.post(
        "https://slack.com/api/chat.postMessage",
        json={"ok": True, "channel": CHANNEL_ID, "ts": "1234567890.123456"},
        status=200,
    )
    responses.post(
        "https://slack.com/api/pins.add",
        json={"ok": False, "error": "missing_scope"},
        status=200,
    )

    with pytest.raises(SlackError, match="pins.add.*missing_scope"):
        post_and_pin_to_slack(BOT_TOKEN, CHANNEL_ID, PAYLOAD)
