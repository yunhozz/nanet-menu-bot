import argparse
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from nanet_menu.app import run
from nanet_menu.errors import NanetMenuError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="국회도서관 오늘의 식단을 Slack으로 전송합니다.")
    parser.add_argument("--date", type=date.fromisoformat, help="기준 날짜(YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Slack 전송 없이 메시지만 출력")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    target = args.date or datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        run(target, dry_run=args.dry_run)
    except NanetMenuError as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
