from collections.abc import Mapping
from datetime import date
from typing import TypedDict

from nanet_menu.models import DailyMenu

_WEEKDAYS = "월화수목금토일"
_SECTION_PRIORITY = {
    ("박물관식당", "중식"): 0,
    ("도서관식당", "중식"): 1,
    ("도서관식당", "석식"): 2,
}
_MAX_BLOCKS = 50


class SlackPayload(TypedDict):
    text: str
    mrkdwn: bool
    blocks: list[dict[str, object]]


def format_failure_alert_payload(
    target: date,
    error: str,
    run_url: str | None = None,
    retry_url: str | None = None,
) -> SlackPayload:
    detail = error[:2800]
    lines = [f"🚨 {target} 국회도서관 식단 알림 실패", detail]
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 {target} 식단 알림 실패",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "plain_text", "text": detail},
        },
    ]
    if run_url:
        lines.append(f"GitHub Actions 실행 로그: {run_url}")
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"<{run_url}|GitHub Actions 실행 로그 보기>",
                    }
                ],
            }
        )
    if retry_url:
        lines.append(f"재시도 화면(dry_run 해제): {retry_url}")
        blocks.insert(
            2,
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "식단 전송 재시도 화면 열기",
                            "emoji": True,
                        },
                        "url": retry_url,
                        "action_id": "retry_daily_menu",
                        "style": "primary",
                    }
                ],
            },
        )
        blocks.insert(
            2,
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "재시도 화면에서 `dry_run`을 해제하고 실행하세요.",
                    }
                ],
            },
        )
    return {"text": "\n".join(lines), "mrkdwn": False, "blocks": blocks}


def format_slack_payloads(
    menu: DailyMenu,
    image_urls: Mapping[str, str] | None = None,
) -> list[SlackPayload]:
    target = menu.menu_date
    title = f"🍽️ {target.month}월 {target.day}일 {_WEEKDAYS[target.weekday()]}요일 국회도서관 식단"
    sections = sorted(
        menu.sections,
        key=lambda section: _SECTION_PRIORITY.get(
            (section.restaurant, section.meal),
            len(_SECTION_PRIORITY),
        ),
    )
    section_groups: list[tuple[list[str], list[dict[str, object]]]] = []
    for section in sections:
        heading = f"{section.restaurant} · {section.meal}"
        if section.calories:
            calories = " / ".join(f"{value:,}" for value in section.calories)
            heading = f"{heading} · {calories} kcal"
        lines = [
            heading,
            *(f"- {item}" for item in section.items),
        ]
        blocks: list[dict[str, object]] = [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {
                                "type": "text",
                                "text": heading,
                                "style": {"bold": True},
                            }
                        ],
                    },
                ],
            }
        ]
        for item in section.items:
            item_block: dict[str, object] = {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text": f"• {item}",
                    "emoji": True,
                },
            }
            image_url = image_urls.get(item) if image_urls else None
            if image_url:
                item_block["accessory"] = {
                    "type": "image",
                    "image_url": image_url,
                    "alt_text": f"{item} 이미지",
                }
            blocks.append(item_block)
        section_groups.append((lines, blocks))

    chunks: list[list[tuple[list[str], list[dict[str, object]]]]] = [[]]
    block_count = 2
    for group in section_groups:
        group_size = len(group[1]) + (1 if chunks[-1] else 0)
        if chunks[-1] and block_count + group_size > _MAX_BLOCKS:
            chunks.append([])
            block_count = 2
            group_size = len(group[1])
        if block_count + group_size > _MAX_BLOCKS:
            raise ValueError("하나의 식당·식사 구분이 Slack 50블록 제한을 초과합니다.")
        chunks[-1].append(group)
        block_count += group_size

    payloads: list[SlackPayload] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_title = f"{title} ({index}/{len(chunks)})" if len(chunks) > 1 else title
        lines = [chunk_title]
        blocks: list[dict[str, object]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": chunk_title,
                    "emoji": True,
                },
            }
        ]
        for group_index, (section_lines, section_blocks) in enumerate(chunk):
            lines.extend(["", *section_lines])
            if group_index:
                blocks.append({"type": "divider"})
            blocks.extend(section_blocks)
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
        payloads.append({"text": "\n".join(lines), "mrkdwn": False, "blocks": blocks})
    return payloads


def format_slack_payload(
    menu: DailyMenu,
    image_urls: Mapping[str, str] | None = None,
) -> SlackPayload:
    payloads = format_slack_payloads(menu, image_urls)
    if len(payloads) != 1:
        raise ValueError("식단이 여러 Slack 메시지로 분할되었습니다.")
    return payloads[0]
