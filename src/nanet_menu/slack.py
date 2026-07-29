import time

import requests

from nanet_menu.errors import SlackError
from nanet_menu.formatter import SlackPayload


def post_to_slack(
    webhook_url: str,
    payload: SlackPayload,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 15.0),
    max_attempts: int = 3,
) -> None:
    client = session or requests.Session()
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(webhook_url, json=payload, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == max_attempts:
                raise SlackError("Slack 전송이 일시적 네트워크 오류로 실패했습니다.") from exc
            time.sleep(0.25 * attempt)
            continue

        if response.status_code == 200 and response.text.strip() == "ok":
            return
        if 500 <= response.status_code < 600 and attempt < max_attempts:
            time.sleep(0.25 * attempt)
            continue
        raise SlackError(
            f"Slack 전송 실패(status={response.status_code}, body={response.text[:100]!r})"
        )
