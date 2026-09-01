import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import holidays
from firebase_functions import scheduler_fn
from firebase_functions.options import Timezone

from nanet_menu.app import run
from nanet_menu.config import RETRY_WORKFLOW_URL
from nanet_menu.formatter import format_failure_alert_payload
from nanet_menu.slack import post_message_to_slack

LOGGER = logging.getLogger(__name__)
SEOUL = ZoneInfo("Asia/Seoul")
KOREAN_HOLIDAYS = holidays.KR()


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
                retry_url=RETRY_WORKFLOW_URL,
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
