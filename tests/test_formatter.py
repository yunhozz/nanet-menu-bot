from datetime import date

from nanet_menu.formatter import format_failure_alert_payload, format_slack_payload
from nanet_menu.models import DailyMenu, MenuSection


def test_slack_message_format():
    menu = DailyMenu(
        date(2026, 7, 29),
        (MenuSection("구내식당", "중식", ("김치찌개", "현미밥")),),
        "주간식단표",
        "https://example.test/notice",
    )

    payload = format_slack_payload(menu)

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
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "김치찌개"}],
                        },
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "현미밥"}],
                        },
                    ],
                },
            ],
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
            },
            {
                "type": "rich_text_list",
                "style": "bullet",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [{"type": "text", "text": "<!here> & 생선"}],
                    },
                    {
                        "type": "rich_text_section",
                        "elements": [{"type": "text", "text": "`특식` ~한정~"}],
                    },
                ],
            },
        ],
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
