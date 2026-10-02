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


def _valid_image_url(value: object) -> str | None:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        return None
    try:
        parsed_url = urlparse(value)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            return None
        _ = parsed_url.port  # Validate that a supplied port is numeric and in range.
    except ValueError:
        return None
    return value


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
        normalized_menu_item = _normalize_match_text(menu_item)
        if not normalized_menu_item:
            LOGGER.info("메뉴명과 일치하는 이미지 검색 결과 없음: %s", menu_item)
            return None

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

        fallback_image_url = None
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            plain_title = (
                html.unescape(_HTML_TAG_RE.sub("", title)) if isinstance(title, str) else ""
            )
            title_matches = isinstance(
                title, str
            ) and normalized_menu_item in _normalize_match_text(title)

            image_url = _valid_image_url(item.get("thumbnail")) or _valid_image_url(
                item.get("link")
            )
            if image_url is None:
                continue

            if title_matches:
                LOGGER.info("이미지 검색 결과 선택(%s): %s", menu_item, plain_title)
                return image_url
            if fallback_image_url is None:
                fallback_image_url = image_url

        if fallback_image_url is not None:
            LOGGER.info("제목 일치 결과가 없어 첫 유효 이미지 사용(%s)", menu_item)
            return fallback_image_url

        LOGGER.info("유효한 이미지 검색 결과 없음: %s", menu_item)
        return None
