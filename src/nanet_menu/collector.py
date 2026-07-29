import json
import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from nanet_menu.config import Settings
from nanet_menu.date_range import normalize_text
from nanet_menu.errors import CollectionError
from nanet_menu.models import Attachment, Notice

BASE_URL = "https://www.nanet.go.kr"
DETAIL_PATH = "/usermadang/notice/noticeDetail.do"
ATTACHMENT_PATH = "/usermadang/notice/getAttachfile.do"
PDF_PATH = "/attachfiles/gongji/"
_VIEWER_RE = re.compile(
    r"newViewerCall\(\s*['\"](?P<system>[^'\"]+\.pdf)['\"]\s*,"
    r"\s*['\"](?P<sequence>\d+)['\"]",
    re.IGNORECASE,
)


class NanetCollector:
    def __init__(
        self,
        settings: Settings | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml",
            }
        )

    def _get(self, url: str, *, referer: str | None = None) -> requests.Response:
        headers = {"Referer": referer} if referer else None
        try:
            response = self.session.get(url, headers=headers, timeout=self.settings.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CollectionError(f"HTTP 요청 실패: {url}") from exc
        return response

    def fetch_notices(self) -> list[Notice]:
        response = self._get(self.settings.list_url, referer=BASE_URL)
        notices = parse_notice_list(response.text)
        if not notices:
            raise CollectionError("공지 목록에서 식단 게시물을 찾지 못했습니다.")
        return notices

    def fetch_attachment(self, notice: Notice) -> Attachment:
        response = self._get(notice.detail_url, referer=self.settings.list_url)
        attachment = parse_detail_attachment(response.text, notice.sequence)
        if attachment:
            return attachment

        metadata_url = f"{BASE_URL}{ATTACHMENT_PATH}?searchNoSeq={notice.sequence}"
        metadata = self._get(metadata_url, referer=notice.detail_url)
        try:
            payload = metadata.json()
            item = payload["data"]["resultList"][0]
            system_filename = item["pfile1"]
            filename = item["prealFile1"]
        except (
            requests.JSONDecodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise CollectionError("상세 페이지에서 PDF 첨부정보를 찾지 못했습니다.") from exc
        if not system_filename or not filename or not system_filename.lower().endswith(".pdf"):
            raise CollectionError("공지의 첫 번째 첨부파일이 PDF가 아닙니다.")
        return _make_attachment(filename, system_filename)

    def download_pdf(self, attachment: Attachment, referer: str) -> bytes:
        response = self._get(attachment.download_url, referer=referer)
        content_type = response.headers.get("Content-Type", "").lower()
        content = response.content
        if (
            len(content) < 1_000
            or not content.startswith(b"%PDF-")
            or ("pdf" not in content_type and "octet-stream" not in content_type)
        ):
            raise CollectionError(
                f"PDF 응답 검증 실패(content-type={content_type!r}, size={len(content)})"
            )
        return content


def parse_notice_list(html: str) -> list[Notice]:
    soup = BeautifulSoup(html, "lxml")
    results: list[Notice] = []
    for link in soup.select("a.detailLink[data-search-no-seq]"):
        title = normalize_text(link.get_text(" ", strip=True))
        if "식단" not in title:
            continue
        row = link.find_parent("tr")
        cells = row.find_all("td") if row else []
        texts = [normalize_text(cell.get_text(" ", strip=True)) for cell in cells]
        registered_text = next(
            (text for text in texts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text)),
            "",
        )
        if not registered_text:
            continue
        sequence = link["data-search-no-seq"].strip()
        display_number = next((text for text in texts if text.isdigit()), sequence)
        detail_url = f"{BASE_URL}{DETAIL_PATH}?searchNoSeq={sequence}"
        results.append(
            Notice(
                sequence=sequence,
                display_number=display_number,
                title=title,
                registered_on=date.fromisoformat(registered_text),
                detail_url=detail_url,
            )
        )
    return results


def parse_detail_attachment(html: str, expected_sequence: str) -> Attachment | None:
    soup = BeautifulSoup(html, "lxml")
    for link in soup.select("a[onclick*='newViewerCall']"):
        match = _VIEWER_RE.search(link.get("onclick", ""))
        if match and match["sequence"] == expected_sequence:
            filename = normalize_text(link.get_text(" ", strip=True))
            return _make_attachment(filename, match["system"])
    return None


def _make_attachment(filename: str, system_filename: str) -> Attachment:
    safe_system_filename = system_filename.rsplit("/", 1)[-1]
    return Attachment(
        filename=filename,
        system_filename=safe_system_filename,
        download_url=urljoin(BASE_URL, PDF_PATH + safe_system_filename),
    )
