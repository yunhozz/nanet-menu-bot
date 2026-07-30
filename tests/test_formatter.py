from datetime import date

from nanet_menu.formatter import (
    format_failure_alert_payload,
    format_slack_payload,
    format_slack_payloads,
)
from nanet_menu.models import DailyMenu, MenuSection


def test_slack_message_format():
    menu = DailyMenu(
        date(2026, 7, 29),
        (MenuSection("구내식당", "중식", ("김치찌개", "현미밥")),),
        "주간식단표",
        "https://example.test/notice",
    )

    payload = format_slack_payload(
        menu,
        {
            "김치찌개": "https://search.pstatic.net/kimchi.jpg",
            "현미밥": "https://search.pstatic.net/rice.jpg",
        },
    )

    assert "🍽️ 7월 29일 수요일 국회도서관 식단" in payload["text"]
    assert "- 김치찌개\n- 현미밥" in payload["text"]
    assert payload["mrkdwn"] is False
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
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {
                            "type": "text",
                            "text": "구내식당 · 중식",
                            "style": {"bold": True},
                        }
                    ],
                },
            ],
        },
        {
            "type": "section",
            "text": {"type": "plain_text", "text": "• 김치찌개", "emoji": True},
            "accessory": {
                "type": "image",
                "image_url": "https://search.pstatic.net/kimchi.jpg",
                "alt_text": "김치찌개 이미지",
            },
        },
        {
            "type": "section",
            "text": {"type": "plain_text", "text": "• 현미밥", "emoji": True},
            "accessory": {
                "type": "image",
                "image_url": "https://search.pstatic.net/rice.jpg",
                "alt_text": "현미밥 이미지",
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
        "박물관식당 · 중식",
        "도서관식당 · 중식",
        "도서관식당 · 석식",
        "본관1식당 · 조식",
        "회관1식당 · 중식",
    ]
    heading_texts = [
        block["elements"][0]["elements"][0]["text"]
        for block in payload["blocks"]
        if block["type"] == "rich_text"
    ]
    heading_indexes = [heading_texts.index(heading) for heading in headings]
    assert heading_indexes == sorted(heading_indexes)


def test_external_menu_text_is_not_interpreted_as_slack_markup():
    menu = DailyMenu(
        date(2026, 7, 29),
        (
            MenuSection(
                "*R&D* <본관>",
                "_중식_",
                ("<!here> & 생선", "`특식` ~한정~"),
            ),
        ),
        "주간식단표",
        "https://example.test/notice",
    )

    payload = format_slack_payload(menu)

    assert payload["mrkdwn"] is False
    assert "*R&D* <본관> · _중식_" in payload["text"]
    assert payload["blocks"][1] == {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [
                    {
                        "type": "text",
                        "text": "*R&D* <본관> · _중식_",
                        "style": {"bold": True},
                    }
                ],
            }
        ],
    }
    assert payload["blocks"][2] == {
        "type": "section",
        "text": {
            "type": "plain_text",
            "text": "• <!here> & 생선",
            "emoji": True,
        },
    }
    assert payload["blocks"][3] == {
        "type": "section",
        "text": {
            "type": "plain_text",
            "text": "• `특식` ~한정~",
            "emoji": True,
        },
    }


def test_failure_alert_is_plain_text_and_links_to_the_workflow_run():
    payload = format_failure_alert_payload(
        date(2026, 7, 29),
        "PDF에서 <!here> *식단*을 찾지 못했습니다.",
        "https://github.com/example/nanet-menu-bot/actions/runs/1234",
    )

    assert payload["mrkdwn"] is False
    assert "PDF에서 <!here> *식단*을 찾지 못했습니다." in payload["text"]
    assert payload["blocks"][1] == {
        "type": "section",
        "text": {
            "type": "plain_text",
            "text": "PDF에서 <!here> *식단*을 찾지 못했습니다.",
        },
    }


def test_large_menu_is_split_without_cutting_sections():
    menu = DailyMenu(
        date(2026, 7, 29),
        tuple(
            MenuSection(f"{index}식당", "중식", tuple(f"{index}-{item}" for item in range(8)))
            for index in range(1, 9)
        ),
        "주간식단표",
        "https://example.test/notice",
    )

    payloads = format_slack_payloads(menu)

    assert len(payloads) == 2
    assert all(len(payload["blocks"]) <= 50 for payload in payloads)
    for index in range(1, 9):
        section_heading = f"{index}식당 · 중식"
        assert sum(section_heading in payload["text"] for payload in payloads) == 1
