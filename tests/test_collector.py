from datetime import date

import pytest
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
