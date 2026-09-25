import html
import logging
import re
import unicodedata
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger(__name__)
_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"
_RESULT_COUNT = 5
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD_RE = re.compile(r"[\W_]+")


def _normalize_match_text(value: str) -> str:
    text = html.unescape(_HTML_TAG_RE.sub("", value))
    text = unicodedata.normalize("NFKC", text)
    return _NON_WORD_RE.sub("", text).casefold()


class NaverImageSearch:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 10.0),
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self.timeout = timeout

    def first_image_url(self, menu_item: str) -> str | None:
        try:
            response = self.session.get(
                _SEARCH_URL,
                params={"query": menu_item, "display": _RESULT_COUNT},
                headers={
                    "X-NCP-APIGW-API-KEY-ID": self.client_id,
                    "X-NCP-APIGW-API-KEY": self.client_secret,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
        except (requests.RequestException, ValueError, AttributeError) as exc:
            LOGGER.warning("이미지 검색 실패(%s): %s", menu_item, exc)
            return None

        if not isinstance(items, list) or not items:
            LOGGER.info("이미지 검색 결과 없음: %s", menu_item)
            return None

        normalized_menu_item = _normalize_match_text(menu_item)
        if not normalized_menu_item:
            LOGGER.info("메뉴명과 일치하는 이미지 검색 결과 없음: %s", menu_item)
            return None

        matched_title = False
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if not isinstance(title, str):
                continue
            plain_title = html.unescape(_HTML_TAG_RE.sub("", title))
            if normalized_menu_item not in _normalize_match_text(plain_title):
                continue
            matched_title = True

            image_url = item.get("thumbnail") or item.get("link")
            if isinstance(image_url, str) and urlparse(image_url).scheme in {"http", "https"}:
                LOGGER.info("이미지 검색 결과 선택(%s): %s", menu_item, plain_title)
                return image_url
            LOGGER.warning("이미지 검색 결과 URL이 올바르지 않음: %s", menu_item)

        if not matched_title:
            LOGGER.info("메뉴명과 일치하는 이미지 검색 결과 없음: %s", menu_item)
        return None
