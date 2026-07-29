import argparse
import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from nanet_menu.app import run
from nanet_menu.errors import NanetMenuError, SlackError
from nanet_menu.formatter import format_failure_alert_payload
from nanet_menu.slack import post_to_slack


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="국회도서관 오늘의 식단을 Slack으로 전송합니다.")
    parser.add_argument("--date", type=date.fromisoformat, help="기준 날짜(YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Slack 전송 없이 메시지만 출력")
    return parser.parse_args()


def _github_run_url() -> str | None:
    server_url = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not all((server_url, repository, run_id)):
        return None
    return f"{server_url}/{repository}/actions/runs/{run_id}"


def _post_failure_alert(target: date, error: NanetMenuError) -> None:
    webhook_url = os.environ.get("SLACK_ALERT_WEBHOOK_URL")
    if not webhook_url:
        return
    payload = format_failure_alert_payload(target, str(error), _github_run_url())
    try:
        post_to_slack(webhook_url, payload)
    except SlackError as alert_error:
        logging.error("Slack 실패 알림 전송 실패: %s", alert_error)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    target = args.date or datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        run(target, dry_run=args.dry_run)
    except NanetMenuError as exc:
        logging.error("%s", exc)
        if not args.dry_run:
            _post_failure_alert(target, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
