import sys
from datetime import date

from nanet_menu import __main__
from nanet_menu.errors import MenuParseError


def test_failed_send_posts_alert_to_separate_webhook(monkeypatch):
    alert_webhook = "https://hooks.slack.test/services/ALERT/SECRET"
    monkeypatch.setattr(
        sys,
        "argv",
        ["nanet-menu", "--date", "2026-07-29"],
    )
    monkeypatch.setenv("SLACK_ALERT_WEBHOOK_URL", alert_webhook)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/nanet-menu-bot")
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")

    def fail_run(target: date, *, dry_run: bool):
        raise MenuParseError("식단을 찾지 못했습니다.")

    sent = []
    monkeypatch.setattr(__main__, "run", fail_run)
    monkeypatch.setattr(
        __main__,
        "post_to_slack",
        lambda webhook_url, payload: sent.append((webhook_url, payload)),
    )

    assert __main__.main() == 1
    assert sent[0][0] == alert_webhook
    assert sent[0][1]["mrkdwn"] is False
    assert "2026-07-29" in sent[0][1]["text"]
    assert "식단을 찾지 못했습니다." in sent[0][1]["text"]
    assert "https://github.com/example/nanet-menu-bot/actions/runs/1234" in sent[0][1]["text"]


def test_failed_dry_run_does_not_post_alert(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["nanet-menu", "--date", "2026-07-29", "--dry-run"],
    )
    monkeypatch.setenv(
        "SLACK_ALERT_WEBHOOK_URL",
        "https://hooks.slack.test/services/ALERT/SECRET",
    )

    def fail_run(target: date, *, dry_run: bool):
        raise MenuParseError("식단을 찾지 못했습니다.")

    monkeypatch.setattr(__main__, "run", fail_run)
    monkeypatch.setattr(
        __main__,
        "post_to_slack",
        lambda *args: raise_if_called(),
    )

    assert __main__.main() == 1


def raise_if_called():
    raise AssertionError("dry-run failure must not send a Slack alert")
