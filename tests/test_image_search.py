import logging

import responses

from nanet_menu.image_search import NaverImageSearch

SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"


@responses.activate
def test_returns_first_result_thumbnail():
    responses.get(
        SEARCH_URL,
        json={
            "items": [
                {
                    "link": "https://example.test/original.jpg",
                    "thumbnail": "https://search.pstatic.net/thumbnail.jpg",
                }
            ]
        },
        status=200,
    )

    result = NaverImageSearch("client-id", "client-secret").first_image_url("김치찌개")

    assert result == "https://search.pstatic.net/thumbnail.jpg"
    request = responses.calls[0].request
    assert request.params == {"query": "김치찌개", "display": "1"}
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "client-id"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "client-secret"


@responses.activate
def test_returns_none_when_no_results():
    responses.get(SEARCH_URL, json={"items": []}, status=200)

    assert NaverImageSearch("client-id", "client-secret").first_image_url("없는 메뉴") is None


@responses.activate
def test_search_failure_does_not_expose_credentials(caplog):
    responses.get(SEARCH_URL, json={"error": "unavailable"}, status=503)
    caplog.set_level(logging.WARNING)

    assert NaverImageSearch("client-id", "client-secret").first_image_url("김치찌개") is None
    assert "이미지 검색 실패(김치찌개)" in caplog.text
    assert "client-id" not in caplog.text
    assert "client-secret" not in caplog.text
