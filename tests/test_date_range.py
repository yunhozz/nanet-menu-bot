from datetime import date

import pytest

from nanet_menu.date_range import normalize_text, parse_title_date_range, select_notice
from nanet_menu.models import Notice


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "주간식단표(2026. 7.27. - 8.02)",
            (date(2026, 7, 27), date(2026, 8, 2)),
        ),
        (
            "주간식단표(2026. 07.13.~ 07.19.)",
            (date(2026, 7, 13), date(2026, 7, 19)),
        ),
        (
            "주간식단표(2026. 6.29. - 7.05)",
            (date(2026, 6, 29), date(2026, 7, 5)),
        ),
        (
            "주간식단표(2026. 12.29. - 1.04)",
            (date(2026, 12, 29), date(2027, 1, 4)),
        ),
    ],
)
def test_parse_title_date_ranges(title, expected):
    assert parse_title_date_range(title) == expected


def test_normalizes_full_width_digits():
    title = "주간식단표(２０２６. ０７.２０. - ０７.２６.)"

    assert "2026" in normalize_text(title)
    assert parse_title_date_range(title) == (
        date(2026, 7, 20),
        date(2026, 7, 26),
    )


def test_selects_notice_covering_target_even_if_an_old_notice_is_newer():
    old = Notice(
        "2",
        "2",
        "주간식단표(2026. 7.20. - 7.26)",
        date(2026, 7, 30),
        "https://example.test/2",
    )
    current = Notice(
        "1",
        "1",
        "주간식단표(2026. 7.27. - 8.02)",
        date(2026, 7, 26),
        "https://example.test/1",
    )

    assert select_notice([old, current], date(2026, 7, 29)) is current
