import logging

import pytest
import responses

from nanet_menu.errors import SlackError
from nanet_menu.slack import post_to_slack

WEBHOOK = "https://hooks.slack.test/services/SECRET/VALUE"


@responses.activate
def test_webhook_success():
    responses.post(WEBHOOK, body="ok", status=200)

    post_to_slack(WEBHOOK, "hello")

    assert responses.calls[0].request.body == b'{"text": "hello"}'


@responses.activate
def test_webhook_4xx_is_not_retried():
    responses.post(WEBHOOK, body="invalid_payload", status=400)

    with pytest.raises(SlackError, match="status=400"):
        post_to_slack(WEBHOOK, "hello")

    assert len(responses.calls) == 1


@responses.activate
def test_webhook_5xx_is_retried(monkeypatch):
    monkeypatch.setattr("nanet_menu.slack.time.sleep", lambda _: None)
    responses.post(WEBHOOK, body="error", status=503)
    responses.post(WEBHOOK, body="error", status=503)
    responses.post(WEBHOOK, body="ok", status=200)

    post_to_slack(WEBHOOK, "hello")

    assert len(responses.calls) == 3


@responses.activate
def test_webhook_url_is_not_logged(caplog):
    responses.post(WEBHOOK, body="invalid_payload", status=400)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(SlackError):
        post_to_slack(WEBHOOK, "hello")

    assert WEBHOOK not in caplog.text
