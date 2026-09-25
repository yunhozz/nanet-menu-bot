import logging

import responses

from nanet_menu.image_search import NaverImageSearch

SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"


@responses.activate
def test_returns_first_matching_result_thumbnail():
    responses.get(
        SEARCH_URL,
        json={
            "items": [
                {
                    "title": "고등어구이 정식",
                    "link": "https://example.test/wrong.jpg",
                    "thumbnail": "https://search.pstatic.net/wrong.jpg",
                },
                {
                    "title": "<b>김치</b> 찌개 맛있게 끓이는 법",
                    "link": "https://example.test/original.jpg",
                    "thumbnail": "https://search.pstatic.net/thumbnail.jpg",
                },
            ]
        },
        status=200,
    )

    result = NaverImageSearch("client-id", "client-secret").first_image_url("김치찌개")

    assert result == "https://search.pstatic.net/thumbnail.jpg"
    request = responses.calls[0].request
    assert request.params == {"query": "김치찌개", "display": "5"}
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "client-id"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "client-secret"


@responses.activate
def test_returns_none_when_no_results_match():
    responses.get(SEARCH_URL, json={"items": []}, status=200)
    responses.get(
        SEARCH_URL,
        json={
            "items": [
                {
                    "title": "고등어구이 정식",
                    "thumbnail": "https://search.pstatic.net/wrong.jpg",
                }
            ]
        },
        status=200,
    )

    assert NaverImageSearch("client-id", "client-secret").first_image_url("없는 메뉴") is None
    assert NaverImageSearch("client-id", "client-secret").first_image_url("김치찌개") is None


@responses.activate
def test_search_failure_does_not_expose_credentials(caplog):
    responses.get(SEARCH_URL, json={"error": "unavailable"}, status=503)
    caplog.set_level(logging.WARNING)

    assert NaverImageSearch("client-id", "client-secret").first_image_url("김치찌개") is None
    assert "이미지 검색 실패(김치찌개)" in caplog.text
    assert "client-id" not in caplog.text
    assert "client-secret" not in caplog.text
