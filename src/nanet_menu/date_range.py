import re
import unicodedata
from datetime import date

from nanet_menu.errors import MenuParseError
from nanet_menu.models import Notice

_RANGE_RE = re.compile(
    r"(?P<year>\d{4})\s*[.]\s*"
    r"(?P<start_month>\d{1,2})\s*[.]\s*"
    r"(?P<start_day>\d{1,2})\s*[.]?\s*"
    r"(?:-|~)\s*"
    r"(?:(?P<end_year>\d{4})\s*[.]\s*)?"
    r"(?:(?P<end_month>\d{1,2})\s*[.]\s*)?"
    r"(?P<end_day>\d{1,2})\s*[.]?"
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def parse_title_date_range(title: str) -> tuple[date, date]:
    normalized = normalize_text(title).replace("–", "-").replace("—", "-")
    match = _RANGE_RE.search(normalized)
    if not match:
        raise MenuParseError(f"게시물 제목에서 날짜 범위를 찾을 수 없습니다: {title}")

    year = int(match["year"])
    start_month = int(match["start_month"])
    start = date(year, start_month, int(match["start_day"]))

    end_year_text = match["end_year"]
    end_month_text = match["end_month"]
    end_month = int(end_month_text) if end_month_text else start_month
    if end_year_text:
        end_year = int(end_year_text)
    elif end_month < start_month:
        end_year = year + 1
    else:
        end_year = year

    try:
        end = date(end_year, end_month, int(match["end_day"]))
    except ValueError:
        # A missing end month such as 6.29 - 7.05 is already handled by the
        # regex; any remaining invalid date must not be guessed.
        raise MenuParseError(f"게시물 제목의 종료일이 올바르지 않습니다: {title}") from None
    if end < start:
        raise MenuParseError(f"게시물 제목의 날짜 범위가 역순입니다: {title}")
    return start, end


def notice_covers(notice: Notice, target: date) -> bool:
    try:
        start, end = parse_title_date_range(notice.title)
    except MenuParseError:
        return False
    return start <= target <= end


def order_notice_candidates(notices: list[Notice], target: date) -> list[Notice]:
    return sorted(
        notices,
        key=lambda notice: (notice_covers(notice, target), notice.registered_on),
        reverse=True,
    )


def select_notice(notices: list[Notice], target: date) -> Notice:
    for notice in order_notice_candidates(notices, target):
        if notice_covers(notice, target):
            return notice
    raise MenuParseError(f"{target.isoformat()}을 포함하는 주간식단표 게시물이 없습니다.")
