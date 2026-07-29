from datetime import date

import pytest

from nanet_menu.app import build_message


@pytest.mark.integration
def test_live_site_dry_run():
    payload = build_message(date.today())
    assert "국회도서관 식단" in payload["text"]
