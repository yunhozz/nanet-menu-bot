from datetime import date

from nanet_menu.formatter import format_slack_payload
from nanet_menu.models import DailyMenu, MenuSection


def test_slack_message_format():
    menu = DailyMenu(
        date(2026, 7, 29),
        (MenuSection("구내식당", "중식", ("김치찌개", "현미밥")),),
        "주간식단표",
        "https://example.test/notice",
    )

    payload = format_slack_payload(menu)

    assert "🍽️ *7월 29일 수요일 국회도서관 식단*" in payload["text"]
    assert "- 김치찌개\n- 현미밥" in payload["text"]
    assert payload["blocks"] == [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🍽️ 7월 29일 수요일 국회도서관 식단",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*구내식당 · 중식*\n• 김치찌개\n• 현미밥",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "<https://example.test/notice|주간식단표 원문 보기>",
                }
            ],
        },
    ]


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

    payload = format_slack_payload(menu)

    headings = [
        "*박물관식당 · 중식*",
        "*도서관식당 · 중식*",
        "*도서관식당 · 석식*",
        "*본관1식당 · 조식*",
        "*회관1식당 · 중식*",
    ]
    section_texts = [
        block["text"]["text"] for block in payload["blocks"] if block["type"] == "section"
    ]
    heading_indexes = [
        next(i for i, text in enumerate(section_texts) if heading in text) for heading in headings
    ]
    assert heading_indexes == sorted(heading_indexes)
