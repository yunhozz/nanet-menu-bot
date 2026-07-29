from datetime import date

from nanet_menu.formatter import format_slack_message
from nanet_menu.models import DailyMenu, MenuSection


def test_slack_message_format():
    menu = DailyMenu(
        date(2026, 7, 29),
        (MenuSection("구내식당", "중식", ("김치찌개", "현미밥")),),
        "주간식단표",
        "https://example.test/notice",
    )

    message = format_slack_message(menu)

    assert "🍽️ *7월 29일 수요일 국회도서관 식단*" in message
    assert "*구내식당 · 중식*" in message
    assert "- 김치찌개\n- 현미밥" in message
    assert "<https://example.test/notice|주간식단표 원문 보기>" in message


def test_priority_sections_come_first_without_reordering_the_rest():
    menu = DailyMenu(
        date(2026, 7, 29),
        (
            MenuSection("본관1식당", "조식", ("아침",)),
            MenuSection("도서관식당", "석식", ("도서관 저녁",)),
            MenuSection("회관1식당", "중식", ("회관 점심",)),
            MenuSection("박물관식당", "중식", ("박물관 점심",)),
            MenuSection("도서관식당", "중식", ("도서관 점심",)),
        ),
        "주간식단표",
        "https://example.test/notice",
    )

    message = format_slack_message(menu)

    headings = [
        "*박물관식당 · 중식*",
        "*도서관식당 · 중식*",
        "*도서관식당 · 석식*",
        "*본관1식당 · 조식*",
        "*회관1식당 · 중식*",
    ]
    assert [message.index(heading) for heading in headings] == sorted(
        message.index(heading) for heading in headings
    )
