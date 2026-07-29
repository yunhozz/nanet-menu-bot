from datetime import date

import pytest

from nanet_menu.errors import MenuParseError
from nanet_menu.models import MenuSection
from nanet_menu.pdf_parser import extract_menu, extract_menu_from_tables


def test_extracts_date_column_and_meals_from_minimal_table():
    table = [
        ["", "구내식당"],
        ["", None],
        ["", None],
        ["7.29\n(수)", "김치찌개\n현미밥\n깍두기"],
        [None, "<저녁> 제육볶음\n잡곡밥"],
        ["7.30\n(목)", "국수"],
    ]

    sections = extract_menu_from_tables([table], date(2026, 7, 29))

    assert sections == (
        MenuSection("구내식당", "중식", ("김치찌개", "현미밥", "깍두기")),
        MenuSection("구내식당", "석식", ("제육볶음", "잡곡밥")),
    )


def test_missing_date_returns_no_sections():
    table = [["", "구내식당"], ["7.28\n(화)", "비빔밥"]]

    assert extract_menu_from_tables([table], date(2026, 7, 29)) == ()


def test_invalid_pdf_raises_clear_error():
    with pytest.raises(MenuParseError, match="PDF"):
        extract_menu(b"not a pdf", date(2026, 7, 29))
