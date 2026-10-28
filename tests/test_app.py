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

    monkeypatch.setattr(app, "post_and_pin_to_slack", fail_if_called)

    assert app.run(date(2026, 7, 29), dry_run=True) == "menu message"
    assert capsys.readouterr().out == "menu message\n"


@pytest.mark.parametrize(
    ("missing_name", "message"),
    [
        ("SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN"),
        ("SLACK_CHANNEL_ID", "SLACK_CHANNEL_ID"),
    ],
)
def test_send_requires_slack_api_environment(monkeypatch, missing_name, message):
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
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")
    monkeypatch.delenv(missing_name, raising=False)
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_SECRET", "client-secret")

    with pytest.raises(NanetMenuError, match=message):
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
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")
    posted = []
    monkeypatch.setattr(
        app,
        "post_and_pin_to_slack",
        lambda token, channel, payload: posted.append((token, channel, payload)),
    )

    result = app.run(date(2026, 7, 29), dry_run=False)

    assert result == "menu 1/2\n\nmenu 2/2"
    assert posted == [
        ("xoxb-test-token", "C0123456789", payloads[0]),
        ("xoxb-test-token", "C0123456789", payloads[1]),
    ]


def test_build_messages_includes_only_museum_lunch_in_output_and_image_search(monkeypatch):
    target = date(2026, 7, 29)
    notice = Notice("1", "1", "주간식단표", target, "https://example.test/notice")
    attachment = Attachment("menu.pdf", "menu.pdf", "https://example.test/menu.pdf")

    class Collector:
        def ensure_budget(self):
            pass

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
            MenuSection("박물관식당", "조식", ("박물관 아침",)),
            MenuSection("박물관식당", "중식", ("박물관 점심",)),
            MenuSection("도서관식당", "중식", ("도서관 점심",)),
            MenuSection("박물관식당", "석식", ("박물관 저녁",)),
        ),
    )
    image_search = ImageSearch()

    payloads = app.build_messages(target, collector=Collector(), image_search=image_search)

    assert "박물관식당 · 중식" in payloads[0]["text"]
    assert "박물관 점심" in payloads[0]["text"]
    assert "도서관식당" not in payloads[0]["text"]
    assert "도서관 점심" not in payloads[0]["text"]
    assert "조식" not in payloads[0]["text"]
    assert "박물관 아침" not in payloads[0]["text"]
    assert "석식" not in payloads[0]["text"]
    assert "박물관 저녁" not in payloads[0]["text"]
    assert image_search.queries == ["박물관 점심"]


def test_prepare_failure_has_explicit_pre_send_evidence(monkeypatch):
    monkeypatch.setattr(
        app, "build_messages", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad"))
    )
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_API_HUB_CLIENT_SECRET", "secret")
    with pytest.raises(NanetMenuError) as caught:
        app.run(date(2026, 7, 29), dry_run=False)
    assert caught.value.failure.stage == "prepare"
    assert caught.value.failure.pre_send_confirmed
    assert isinstance(caught.value.__cause__, ValueError)


def test_partial_post_stops_and_preserves_progress(monkeypatch):
    from nanet_menu.errors import SlackError

    monkeypatch.setattr(
        app,
        "build_messages",
        lambda *a, **k: [{"text": str(i), "mrkdwn": False, "blocks": []} for i in range(3)],
    )
    for name in (
        "NAVER_API_HUB_CLIENT_ID",
        "NAVER_API_HUB_CLIENT_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
    ):
        monkeypatch.setenv(name, "value")
    calls = []

    def post(*args):
        calls.append(args)
        if len(calls) == 2:
            raise SlackError("unknown", method="chat.postMessage", outcome_unknown=True)
        return "123.456"

    monkeypatch.setattr(app, "post_and_pin_to_slack", post)
    with pytest.raises(NanetMenuError) as caught:
        app.run(date(2026, 7, 29), dry_run=False)
    assert len(calls) == 2
    assert caught.value.failure.posted_count == 1
    assert caught.value.failure.message_timestamps == ("123.456",)
    assert caught.value.failure.payload_index == 2
    assert caught.value.failure.outcome_unknown


def test_pin_failure_preserves_posted_timestamp(monkeypatch):
    from nanet_menu.errors import SlackError

    monkeypatch.setattr(
        app, "build_messages", lambda *a, **k: [{"text": "menu", "mrkdwn": False, "blocks": []}]
    )
    for name in (
        "NAVER_API_HUB_CLIENT_ID",
        "NAVER_API_HUB_CLIENT_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
    ):
        monkeypatch.setenv(name, "value")

    def fail(*args):
        raise SlackError("pin", method="pins.add", message_ts="123.456")

    monkeypatch.setattr(app, "post_and_pin_to_slack", fail)
    with pytest.raises(NanetMenuError) as caught:
        app.run(date(2026, 7, 29), dry_run=False)
    assert caught.value.failure.stage == "pin"
    assert caught.value.failure.posted_count == 1
    assert caught.value.failure.message_timestamps == ("123.456",)


def test_candidate_budget_exhaustion_preserves_earlier_causes(monkeypatch):
    from nanet_menu.errors import CollectionError, HttpFailure, MenuParseError

    target = date(2026, 7, 29)
    notices = [
        Notice("1", "1", "first", target, "https://example.test/1"),
        Notice("2", "2", "second", target, "https://example.test/2"),
    ]
    first = CollectionError(
        "first failed", failure=HttpFailure(kind="http", status=503, attempts=5)
    )
    exhausted = CollectionError(
        "budget exhausted", failure=HttpFailure(kind="budget", budget_exhausted=True)
    )

    class Collector:
        def __init__(self):
            self.checks = 0
            self.fetches = []

        def fetch_notices(self):
            return notices

        def ensure_budget(self):
            self.checks += 1
            if self.checks == 2:
                raise exhausted

        def fetch_attachment(self, notice):
            self.fetches.append(notice)
            raise first

    collector = Collector()
    monkeypatch.setattr(app, "order_notice_candidates", lambda notices, target: notices)
    with pytest.raises(MenuParseError) as caught:
        app.build_messages(target, collector=collector)
    assert caught.value.causes == (first, exhausted)
    assert caught.value.causes[0].failure.status == 503
    assert caught.value.causes[1].failure.budget_exhausted
    assert collector.fetches == [notices[0]]


@pytest.mark.parametrize("pin_json", [None, []])
def test_malformed_pin_response_preserves_confirmed_post(monkeypatch, pin_json):
    import responses

    payloads = [{"text": str(i), "mrkdwn": False, "blocks": []} for i in range(2)]
    monkeypatch.setattr(app, "build_messages", lambda *args, **kwargs: payloads)
    for name in (
        "NAVER_API_HUB_CLIENT_ID",
        "NAVER_API_HUB_CLIENT_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
    ):
        monkeypatch.setenv(name, "value")
    with responses.RequestsMock() as mocked:
        mocked.post("https://slack.com/api/chat.postMessage", json={"ok": True, "ts": "123.456"})
        mocked.post(
            "https://slack.com/api/pins.add",
            body="null" if pin_json is None else "[]",
            content_type="application/json",
        )
        with pytest.raises(NanetMenuError) as caught:
            app.run(date(2026, 7, 29), dry_run=False)
        assert len(mocked.calls) == 2
    assert caught.value.failure.stage == "pin"
    assert caught.value.failure.posted_count == 1
    assert caught.value.failure.message_timestamps == ("123.456",)
    assert caught.value.failure.outcome_unknown
