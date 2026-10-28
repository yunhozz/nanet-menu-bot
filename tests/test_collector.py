from datetime import date

import pytest
import requests
import responses

from nanet_menu.collector import NanetCollector, parse_detail_attachment, parse_notice_list
from nanet_menu.errors import CollectionError
from nanet_menu.models import Attachment


def test_parse_notice_sequence_and_display_number(fixture_dir):
    notices = parse_notice_list((fixture_dir / "notice_list.html").read_text())

    assert len(notices) == 1
    assert notices[0].sequence == "8557"
    assert notices[0].display_number == "1763"
    assert notices[0].registered_on == date(2026, 7, 26)


def test_parse_detail_attachment(fixture_dir):
    attachment = parse_detail_attachment(
        (fixture_dir / "notice_detail.html").read_text(),
        "8557",
    )

    assert attachment is not None
    assert attachment.system_filename == "1785038488346.pdf"
    assert attachment.download_url.endswith("/attachfiles/gongji/1785038488346.pdf")


@responses.activate
def test_download_rejects_html():
    attachment = Attachment("menu.pdf", "menu.pdf", "https://example.test/menu.pdf")
    responses.get(
        attachment.download_url,
        body=b"<html>error</html>",
        content_type="text/html",
    )

    with pytest.raises(CollectionError, match="PDF 응답 검증 실패"):
        NanetCollector().download_pdf(attachment, "https://example.test/detail")


@responses.activate
def test_get_retries_connection_failures_and_then_succeeds(monkeypatch):
    url = "https://example.test/notices"
    responses.get(url, body=requests.ConnectTimeout("timed out"))
    responses.get(url, body=requests.ConnectTimeout("timed out"))
    responses.get(url, body="ok", status=200)
    sleeps = []
    monkeypatch.setattr("nanet_menu.collector.time.sleep", sleeps.append)

    response = NanetCollector()._get(url)

    assert response.text == "ok"
    assert len(responses.calls) == 3
    assert sleeps == [5.0, 10.0]


@responses.activate
def test_get_recovers_on_fifth_connection_attempt(monkeypatch):
    url = "https://example.test/notices"
    for _ in range(4):
        responses.get(url, body=requests.ConnectTimeout("timed out"))
    responses.get(url, body="ok", status=200)
    sleeps = []
    monkeypatch.setattr("nanet_menu.collector.time.sleep", sleeps.append)

    response = NanetCollector()._get(url)

    assert response.text == "ok"
    assert len(responses.calls) == 5
    assert sleeps == [5.0, 10.0, 20.0, 30.0]


@responses.activate
def test_get_reports_final_connection_error_type(monkeypatch):
    url = "https://example.test/notices"
    for _ in range(5):
        responses.get(url, body=requests.ConnectTimeout("timed out"))
    monkeypatch.setattr("nanet_menu.collector.time.sleep", lambda _: None)

    with pytest.raises(CollectionError, match="ConnectTimeout"):
        NanetCollector()._get(url)


@responses.activate
def test_get_does_not_retry_non_retryable_http_error(monkeypatch):
    url = "https://example.test/notices"
    responses.get(url, status=404)
    sleeps = []
    monkeypatch.setattr("nanet_menu.collector.time.sleep", sleeps.append)

    with pytest.raises(CollectionError, match="HTTP 404"):
        NanetCollector()._get(url)

    assert len(responses.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [429, 500])
@responses.activate
def test_get_retries_retryable_http_status(monkeypatch, status):
    url = "https://example.test/notices"
    responses.get(url, status=status)
    responses.get(url, body="ok", status=200)
    sleeps = []
    monkeypatch.setattr("nanet_menu.collector.time.sleep", sleeps.append)

    response = NanetCollector()._get(url)

    assert response.text == "ok"
    assert len(responses.calls) == 2
    assert sleeps == [5.0]


def test_collection_budget_is_shared_between_requests(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("nanet_menu.collector.time.monotonic", lambda: clock[0])
    collector = NanetCollector()
    collector.ensure_budget()
    clock[0] = 1000.0
    monkeypatch.setattr(collector.session, "get", lambda *args, **kwargs: requests.Response())
    collector.session.get = lambda *args, **kwargs: _ok_response()
    collector._get("https://example.test/one")
    clock[0] = 1180.0
    with pytest.raises(CollectionError) as caught:
        collector._get("https://example.test/two")
    assert caught.value.failure.budget_exhausted


def _ok_response():
    response = requests.Response()
    response.status_code = 200
    return response


def test_timeout_failure_preserves_attempt_details(monkeypatch):
    collector = NanetCollector()
    monkeypatch.setattr(
        collector.session,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectTimeout()),
    )
    monkeypatch.setattr("nanet_menu.collector.time.sleep", lambda _: None)
    with pytest.raises(CollectionError) as caught:
        collector._get("https://example.test")
    assert caught.value.failure.exception_type == "ConnectTimeout"
    assert caught.value.failure.attempts == 5


def test_budget_limits_wait_and_prevents_another_get(monkeypatch):
    clock = [0.0]
    calls = []
    delays = []
    monkeypatch.setattr("nanet_menu.collector.time.monotonic", lambda: clock[0])

    def sleep(seconds):
        delays.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("nanet_menu.collector.time.sleep", sleep)
    collector = NanetCollector()

    def get(*args, **kwargs):
        calls.append(kwargs["timeout"])
        clock[0] = 178.0
        raise requests.ConnectTimeout()

    monkeypatch.setattr(collector.session, "get", get)
    with pytest.raises(CollectionError) as caught:
        collector._get("https://example.test")
    assert delays == [2.0]
    assert calls == [(10.0, 30.0)]
    assert caught.value.failure.attempts == 1
    assert caught.value.failure.budget_exhausted


def test_remaining_budget_caps_next_request_timeout(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr("nanet_menu.collector.time.monotonic", lambda: clock[0])
    collector = NanetCollector()

    def get(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return _ok_response()

    monkeypatch.setattr(collector.session, "get", get)
    collector._get("https://example.test/one")
    clock[0] = 175.0
    collector._get("https://example.test/two")
    assert calls == [(10.0, 30.0), (5.0, 5.0)]
