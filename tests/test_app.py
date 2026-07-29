from datetime import date

import pytest

from nanet_menu import app
from nanet_menu.errors import NanetMenuError


def test_dry_run_does_not_call_slack(monkeypatch, capsys):
    payload = {"text": "menu message", "mrkdwn": False, "blocks": []}
    monkeypatch.setattr(app, "build_message", lambda target: payload)

    def fail_if_called(*args, **kwargs):
        pytest.fail("Slack must not be called in dry-run mode")

    monkeypatch.setattr(app, "post_to_slack", fail_if_called)

    assert app.run(date(2026, 7, 29), dry_run=True) == "menu message"
    assert capsys.readouterr().out == "menu message\n"


def test_send_requires_webhook_environment(monkeypatch):
    monkeypatch.setattr(
        app,
        "build_message",
        lambda target: {"text": "menu message", "mrkdwn": False, "blocks": []},
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    with pytest.raises(NanetMenuError, match="SLACK_WEBHOOK_URL"):
        app.run(date(2026, 7, 29), dry_run=False)
