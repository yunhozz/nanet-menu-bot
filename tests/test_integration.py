from datetime import date

import pytest

from nanet_menu.app import build_messages


@pytest.mark.integration
def test_live_site_dry_run():
    payloads = build_messages(date.today())
    assert "국회도서관 식단" in payloads[0]["text"]
