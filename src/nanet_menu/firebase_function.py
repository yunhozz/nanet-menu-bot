import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from firebase_functions import scheduler_fn
from firebase_functions.options import Timezone

from nanet_menu.app import run
from nanet_menu.errors import NanetMenuError, SlackError
from nanet_menu.formatter import format_failure_alert_payload
from nanet_menu.slack import post_to_slack

LOGGER = logging.getLogger(__name__)
SEOUL = ZoneInfo("Asia/Seoul")


def _post_failure_alert(target: date, error: NanetMenuError) -> None:
    webhook_url = os.environ.get("SLACK_ALERT_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        post_to_slack(
            webhook_url,
            format_failure_alert_payload(target, str(error), source_url=None),
        )
    except SlackError as alert_error:
        LOGGER.error("Slack 실패 알림 전송 실패: %s", alert_error)


def _post_daily_menu(target: date | None = None) -> None:
    delivery_date = target or datetime.now(SEOUL).date()
    try:
        run(delivery_date, dry_run=False)
    except NanetMenuError as error:
        LOGGER.error("%s", error)
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
