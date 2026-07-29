from typing import TypedDict

from nanet_menu.models import DailyMenu

_WEEKDAYS = "월화수목금토일"
_SECTION_PRIORITY = {
    ("박물관식당", "중식"): 0,
    ("도서관식당", "중식"): 1,
    ("도서관식당", "석식"): 2,
}


class SlackPayload(TypedDict):
    text: str
    mrkdwn: bool
    blocks: list[dict[str, object]]


def format_slack_payload(menu: DailyMenu) -> SlackPayload:
    target = menu.menu_date
    lines = [f"🍽️ {target.month}월 {target.day}일 {_WEEKDAYS[target.weekday()]}요일 국회도서관 식단"]
    sections = sorted(
        menu.sections,
        key=lambda section: _SECTION_PRIORITY.get(
            (section.restaurant, section.meal),
            len(_SECTION_PRIORITY),
        ),
    )
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": (
                    f"🍽️ {target.month}월 {target.day}일 "
                    f"{_WEEKDAYS[target.weekday()]}요일 국회도서관 식단"
                ),
                "emoji": True,
            },
        }
    ]
    for index, section in enumerate(sections):
        lines.extend(
            [
                "",
                f"{section.restaurant} · {section.meal}",
                *(f"- {item}" for item in section.items),
            ]
        )
        if index:
            blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {
                                "type": "text",
                                "text": f"{section.restaurant} · {section.meal}",
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
                                "elements": [{"type": "text", "text": item}],
                            }
                            for item in section.items
                        ],
                    },
                ],
            }
        )
    lines.extend(["", f"주간식단표 원문 보기: {menu.source_url}"])
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{menu.source_url}|주간식단표 원문 보기>",
                }
            ],
        }
    )
    return {"text": "\n".join(lines), "mrkdwn": False, "blocks": blocks}
