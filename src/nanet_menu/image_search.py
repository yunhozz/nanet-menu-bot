import logging
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger(__name__)
_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"


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
                params={"query": menu_item, "display": 1},
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

        if not items or not isinstance(items[0], dict):
            LOGGER.info("이미지 검색 결과 없음: %s", menu_item)
            return None

        image_url = items[0].get("thumbnail") or items[0].get("link")
        if not isinstance(image_url, str) or urlparse(image_url).scheme not in {"http", "https"}:
            LOGGER.warning("이미지 검색 결과 URL이 올바르지 않음: %s", menu_item)
            return None
        return image_url
