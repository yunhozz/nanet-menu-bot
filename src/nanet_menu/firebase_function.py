import hashlib
import hmac
import json
import logging
import os
import time
from datetime import date, datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import holidays
import requests
from firebase_functions import https_fn, scheduler_fn
from firebase_functions.options import Timezone

from nanet_menu.app import run
from nanet_menu.errors import SlackError
from nanet_menu.formatter import format_failure_alert_payload
from nanet_menu.slack import post_message_to_slack, update_message_to_slack

LOGGER = logging.getLogger(__name__)
SEOUL = ZoneInfo("Asia/Seoul")
KOREAN_HOLIDAYS = holidays.KR()
_GITHUB_DISPATCH_URL = (
    "https://api.github.com/repos/yunhozz/nanet-menu-bot/"
    "actions/workflows/daily-menu.yml/dispatches"
)
_RETRY_ACTION_ID = "retry_daily_menu"


def _post_failure_alert(target: date, error: Exception) -> None:
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    if not bot_token or not channel_id:
        LOGGER.error("Slack 실패 알림 전송 실패: Bot Token 또는 채널 ID가 없습니다.")
        return
    LOGGER.info("Slack 실패 알림 전송 시작")
    try:
        post_message_to_slack(
            bot_token,
            channel_id,
            format_failure_alert_payload(
                target,
                str(error),
                retry_date=target,
            ),
        )
    except Exception:
        LOGGER.exception("Slack 실패 알림 전송 실패")
        return
    LOGGER.info("Slack 실패 알림 전송 완료")


def _post_daily_menu(target: date | None = None) -> None:
    delivery_date = target or datetime.now(SEOUL).date()
    holiday_name = KOREAN_HOLIDAYS.get(delivery_date)
    if holiday_name:
        LOGGER.info(
            "공휴일이므로 메뉴 알림을 건너뜁니다: %s (%s)",
            delivery_date.isoformat(),
            holiday_name,
        )
        return
    try:
        run(delivery_date, dry_run=False)
    except Exception as error:
        LOGGER.exception("식단 알림 실행 실패: %s", error)
        _post_failure_alert(delivery_date, error)
        raise


@scheduler_fn.on_schedule(
    schedule="0 10 * * 1-5",
    timezone=Timezone("Asia/Seoul"),
    region="asia-northeast3",
    timeout_sec=300,
    max_instances=1,
    concurrency=1,
    retry_count=0,
    secrets=[
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "NAVER_API_HUB_CLIENT_ID",
        "NAVER_API_HUB_CLIENT_SECRET",
    ],
)
def post_daily_menu(_event: scheduler_fn.ScheduledEvent) -> None:
    """Post the weekday menu at 10:00 Asia/Seoul."""
    _post_daily_menu()


def _slack_response(message: str) -> https_fn.Response:
    return https_fn.Response(
        json.dumps({"response_type": "ephemeral", "text": message}, ensure_ascii=False),
        status=200,
        content_type="application/json; charset=utf-8",
    )


def _remove_retry_button(
    bot_token: str,
    channel_id: str,
    message: object,
) -> None:
    if not isinstance(message, dict):
        return
    timestamp = message.get("ts")
    blocks = message.get("blocks")
    if not isinstance(timestamp, str) or not isinstance(blocks, list):
        return

    updated_blocks: list[dict[str, object]] = []
    removed = False
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "actions":
            if isinstance(block, dict):
                updated_blocks.append(block)
            continue
        elements = block.get("elements")
        if not isinstance(elements, list):
            updated_blocks.append(block)
            continue
        remaining = [
            element
            for element in elements
            if not isinstance(element, dict) or element.get("action_id") != _RETRY_ACTION_ID
        ]
        removed = removed or len(remaining) != len(elements)
        if remaining:
            updated_blocks.append({**block, "elements": remaining})

    if removed:
        update_message_to_slack(
            bot_token,
            channel_id,
            timestamp,
            {"text": message.get("text", ""), "blocks": updated_blocks},
            timeout=(0.25, 0.5),
            max_attempts=1,
        )


@https_fn.on_request(
    region="asia-northeast3",
    timeout_sec=10,
    secrets=[
        "SLACK_SIGNING_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "GITHUB_ACTIONS_TOKEN",
    ],
)
def retry_daily_menu(request: https_fn.Request) -> https_fn.Response:
    """Accept signed Slack button requests and dispatch the failed menu date."""
    if request.method != "POST":
        return https_fn.Response("Method not allowed", status=405)

    signing_secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not signing_secret:
        return https_fn.Response("Slack signing secret is not configured", status=503)

    raw_body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not timestamp.isdigit() or len(timestamp) > 12:
        return https_fn.Response("Invalid Slack request", status=401)
    signed_at = int(timestamp)
    if not 0 <= int(time.time()) - signed_at <= 300:
        return https_fn.Response("Invalid Slack request", status=401)
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return https_fn.Response("Invalid Slack request", status=401)

    try:
        payload_value = parse_qs(raw_body.decode("utf-8")).get("payload", [""])[0]
        payload = json.loads(payload_value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return https_fn.Response("Invalid Slack payload", status=400)
    if not isinstance(payload, dict):
        return https_fn.Response("Invalid Slack payload", status=400)

    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    channel = payload.get("channel")
    actions = payload.get("actions")
    action = actions[0] if isinstance(actions, list) and actions else None
    target_value = action.get("value") if isinstance(action, dict) else None
    if (
        not channel_id
        or payload.get("type") != "block_actions"
        or not isinstance(channel, dict)
        or channel.get("id") != channel_id
        or not isinstance(action, dict)
        or action.get("action_id") != _RETRY_ACTION_ID
        or not isinstance(target_value, str)
    ):
        return https_fn.Response("Invalid retry action", status=400)
    try:
        target = date.fromisoformat(target_value)
    except ValueError:
        return https_fn.Response("Invalid retry date", status=400)
    if target.isoformat() != target_value:
        return https_fn.Response("Invalid retry date", status=400)

    github_token = os.environ.get("GITHUB_ACTIONS_TOKEN")
    if not github_token:
        LOGGER.error("GitHub Actions 재시도 토큰이 설정되지 않았습니다.")
        return _slack_response(
            "서버에서 GitHub Actions 인증 정보를 찾지 못해 재시도를 시작하지 못했습니다."
        )

    try:
        response = requests.post(
            _GITHUB_DISPATCH_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "ref": "master",
                "inputs": {"dry_run": "false", "target_date": target.isoformat()},
            },
            timeout=(0.5, 1.0),
        )
    except requests.RequestException:
        LOGGER.exception("GitHub Actions 재시도 요청이 네트워크 오류로 실패했습니다.")
        return _slack_response("재시도 요청에 실패했습니다. 잠시 후 다시 시도해 주세요.")
    if response.status_code not in (200, 204):
        LOGGER.error("GitHub Actions 재시도 요청 실패(status=%s)", response.status_code)
        return _slack_response("재시도 요청에 실패했습니다. 저장소 Actions 권한을 확인해 주세요.")

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if bot_token:
        try:
            _remove_retry_button(
                bot_token,
                channel_id,
                payload.get("message"),
            )
        except SlackError:
            LOGGER.exception("재시도 시작 후 원본 Slack 버튼 제거에 실패했습니다.")
    else:
        LOGGER.error("Slack Bot Token이 설정되지 않아 원본 재시도 버튼을 제거하지 못합니다.")
    return _slack_response(f"{target.isoformat()} 식단 재시도를 시작했습니다.")
