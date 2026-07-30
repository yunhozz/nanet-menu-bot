from datetime import date

import pytest

from nanet_menu import app
from nanet_menu.errors import NanetMenuError
from nanet_menu.models import Attachment, MenuSection, Notice


def test_dry_run_does_not_call_slack(monkeypatch, capsys):
    payload = {"text": "menu message", "mrkdwn": False, "blocks": []}
    monkeypatch.setattr(app, "build_messages", lambda target, image_search=None: [payload])
    monkeypatch.delenv("NAVER_API_HUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_API_HUB_CLIENT_SECRET", raising=False)

    def fail_if_called(*args, **kwargs):
        pytest.fail("Slack must not be called in dry-run mode")

    monkeypatch.setattr(app, "post_to_slack", fail_if_called)

    assert app.run(date(2026, 7, 29), dry_run=True) == "menu message"
    assert capsys.readouterr().out == "menu message\n"


def test_send_requires_webhook_environment(monkeypatch):
    monkeypatch.setattr(
        app,
        "build_messages",
        lambda target, image_search=None: [
            {
                "text": "menu message",
                "mrkdwn": False,
                "blocks": [],
            }
        ],
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_SECRET", "client-secret")

    with pytest.raises(NanetMenuError, match="SLACK_WEBHOOK_URL"):
        app.run(date(2026, 7, 29), dry_run=False)


def test_send_requires_image_search_credentials(monkeypatch):
    monkeypatch.delenv("NAVER_API_HUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_API_HUB_CLIENT_SECRET", raising=False)

    with pytest.raises(NanetMenuError, match="NAVER_API_HUB_CLIENT_ID"):
        app.run(date(2026, 7, 29), dry_run=False)


def test_large_menu_posts_each_split_payload(monkeypatch):
    payloads = [
        {"text": "menu 1/2", "mrkdwn": False, "blocks": []},
        {"text": "menu 2/2", "mrkdwn": False, "blocks": []},
    ]
    monkeypatch.setattr(app, "build_messages", lambda target, image_search=None: payloads)
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/test")
    posted = []
    monkeypatch.setattr(app, "post_to_slack", lambda webhook_url, payload: posted.append(payload))

    result = app.run(date(2026, 7, 29), dry_run=False)

    assert result == "menu 1/2\n\nmenu 2/2"
    assert posted == payloads


def test_build_messages_excludes_dinner_from_output_and_image_search(monkeypatch):
    target = date(2026, 7, 29)
    notice = Notice("1", "1", "주간식단표", target, "https://example.test/notice")
    attachment = Attachment("menu.pdf", "menu.pdf", "https://example.test/menu.pdf")

    class Collector:
        def fetch_notices(self):
            return [notice]

        def fetch_attachment(self, selected_notice):
            assert selected_notice == notice
            return attachment

        def download_pdf(self, selected_attachment, detail_url):
            assert selected_attachment == attachment
            assert detail_url == notice.detail_url
            return b"pdf"

    class ImageSearch:
        def __init__(self):
            self.queries = []

        def first_image_url(self, menu_item):
            self.queries.append(menu_item)
            return None

    monkeypatch.setattr(app, "order_notice_candidates", lambda notices, date_: notices)
    monkeypatch.setattr(
        app,
        "extract_menu",
        lambda pdf_bytes, date_: (
            MenuSection("도서관식당", "중식", ("점심",)),
            MenuSection("도서관식당", "석식", ("저녁",)),
        ),
    )
    image_search = ImageSearch()

    payloads = app.build_messages(target, collector=Collector(), image_search=image_search)

    assert "도서관식당 · 중식" in payloads[0]["text"]
    assert "석식" not in payloads[0]["text"]
    assert "저녁" not in payloads[0]["text"]
    assert image_search.queries == ["점심"]
