from nanet_menu.models import DailyMenu

_WEEKDAYS = "월화수목금토일"


def format_slack_message(menu: DailyMenu) -> str:
    target = menu.menu_date
    lines = [
        f"🍽️ *{target.month}월 {target.day}일 {_WEEKDAYS[target.weekday()]}요일 국회도서관 식단*"
    ]
    for section in menu.sections:
        lines.extend(
            [
                "",
                f"*{section.restaurant} · {section.meal}*",
                *(f"- {item}" for item in section.items),
            ]
        )
    lines.extend(["", f"<{menu.source_url}|주간식단표 원문 보기>"])
    return "\n".join(lines)
