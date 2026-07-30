import logging
import os
from datetime import date

from nanet_menu.collector import NanetCollector
from nanet_menu.date_range import order_notice_candidates
from nanet_menu.errors import MenuParseError, NanetMenuError
from nanet_menu.formatter import SlackPayload, format_slack_payloads
from nanet_menu.image_search import NaverImageSearch
from nanet_menu.models import DailyMenu
from nanet_menu.pdf_parser import extract_menu
from nanet_menu.slack import post_to_slack

LOGGER = logging.getLogger(__name__)


def build_messages(
    target: date,
    collector: NanetCollector | None = None,
    image_search: NaverImageSearch | None = None,
) -> list[SlackPayload]:
    client = collector or NanetCollector()
    LOGGER.info("목록 수집: 식단 공지 검색")
    notices = client.fetch_notices()
    errors: list[str] = []
    for notice in order_notice_candidates(notices, target)[:5]:
        LOGGER.info("게시물 선택: %s", notice.title)
        try:
            attachment = client.fetch_attachment(notice)
            LOGGER.info("PDF 다운로드: %s", attachment.filename)
            pdf_bytes = client.download_pdf(attachment, notice.detail_url)
            LOGGER.info("PDF 파싱")
            sections = tuple(
                section for section in extract_menu(pdf_bytes, target) if section.meal == "중식"
            )
            LOGGER.info("오늘 식단 선택: %s (%d개 구분)", target.isoformat(), len(sections))
            menu = DailyMenu(target, sections, notice.title, notice.detail_url)
            image_urls: dict[str, str] = {}
            if image_search:
                LOGGER.info("메뉴 이미지 검색")
                for item in dict.fromkeys(
                    item for section in menu.sections for item in section.items
                ):
                    image_url = image_search.first_image_url(item)
                    if image_url:
                        image_urls[item] = image_url
            return format_slack_payloads(menu, image_urls)
        except NanetMenuError as exc:
            errors.append(f"{notice.title}: {exc}")
            LOGGER.warning("후보 게시물 처리 실패: %s", exc)
    detail = "; ".join(errors)
    raise MenuParseError(f"{target.isoformat()} 식단을 최근 공지에서 찾지 못했습니다. {detail}")


def run(target: date, *, dry_run: bool) -> str:
    client_id = os.environ.get("NAVER_API_HUB_CLIENT_ID")
    client_secret = os.environ.get("NAVER_API_HUB_CLIENT_SECRET")
    if bool(client_id) != bool(client_secret):
        raise NanetMenuError(
            "이미지 검색 단계: NAVER API HUB Client ID와 Client Secret을 모두 설정해야 합니다."
        )
    if not dry_run and not client_id:
        raise NanetMenuError(
            "이미지 검색 단계: NAVER_API_HUB_CLIENT_ID와 "
            "NAVER_API_HUB_CLIENT_SECRET 환경변수가 없습니다."
        )
    image_search = NaverImageSearch(client_id, client_secret) if client_id else None
    payloads = build_messages(target, image_search=image_search)
    text = "\n\n".join(payload["text"] for payload in payloads)
    if dry_run:
        print(text)
        return text
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        raise NanetMenuError("Slack 전송 단계: SLACK_WEBHOOK_URL 환경변수가 없습니다.")
    LOGGER.info("Slack 전송")
    for payload in payloads:
        post_to_slack(webhook_url, payload)
    return text
