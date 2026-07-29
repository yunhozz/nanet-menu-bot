from nanet_menu.models import DailyMenu

_WEEKDAYS = "월화수목금토일"
_SECTION_PRIORITY = {
    ("박물관식당", "중식"): 0,
    ("도서관식당", "중식"): 1,
    ("도서관식당", "석식"): 2,
}


def format_slack_message(menu: DailyMenu) -> str:
    target = menu.menu_date
    lines = [
        f"🍽️ *{target.month}월 {target.day}일 {_WEEKDAYS[target.weekday()]}요일 국회도서관 식단*"
    ]
    sections = sorted(
        menu.sections,
        key=lambda section: _SECTION_PRIORITY.get(
            (section.restaurant, section.meal),
            len(_SECTION_PRIORITY),
        ),
    )
    for section in sections:
        lines.extend(
            [
                "",
                f"*{section.restaurant} · {section.meal}*",
                *(f"- {item}" for item in section.items),
            ]
        )
    lines.extend(["", f"<{menu.source_url}|주간식단표 원문 보기>"])
    return "\n".join(lines)
