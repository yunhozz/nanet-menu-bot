import time
from typing import Any

import requests

from nanet_menu.errors import SlackError
from nanet_menu.formatter import SlackPayload

SLACK_API_URL = "https://slack.com/api"


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
        if response.status_code == 429 and attempt < max_attempts:
            retry_after = float(response.headers["Retry-After"])
            time.sleep(max(0.0, retry_after))
            continue
        if 500 <= response.status_code < 600 and attempt < max_attempts:
            time.sleep(0.25 * attempt)
            continue
        raise SlackError(
            f"Slack 전송 실패(status={response.status_code}, body={response.text[:100]!r})"
        )


def _call_slack_api(
    client: requests.Session,
    method: str,
    token: str,
    payload: dict[str, Any],
    timeout: tuple[float, float],
    max_attempts: int,
) -> dict[str, Any]:
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(
                f"{SLACK_API_URL}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == max_attempts:
                raise SlackError(f"Slack {method} 호출이 네트워크 오류로 실패했습니다.") from exc
            time.sleep(0.25 * attempt)
            continue

        if response.status_code == 429 and attempt < max_attempts:
            retry_after = float(response.headers["Retry-After"])
            time.sleep(max(0.0, retry_after))
            continue
        if 500 <= response.status_code < 600 and attempt < max_attempts:
            time.sleep(0.25 * attempt)
            continue
        if response.status_code != 200:
            raise SlackError(f"Slack {method} 실패(status={response.status_code})")

        try:
            result = response.json()
        except requests.JSONDecodeError as exc:
            raise SlackError(f"Slack {method} 응답이 올바른 JSON이 아닙니다.") from exc
        if result.get("ok"):
            return result
        raise SlackError(f"Slack {method} 실패(error={result.get('error', 'unknown')})")

    raise AssertionError("unreachable")


def post_and_pin_to_slack(
    bot_token: str,
    channel_id: str,
    payload: SlackPayload,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 15.0),
    max_attempts: int = 3,
) -> None:
    client = session or requests.Session()
    message = _call_slack_api(
        client,
        "chat.postMessage",
        bot_token,
        {"channel": channel_id, **payload},
        timeout,
        max_attempts,
    )
    timestamp = message.get("ts")
    if not isinstance(timestamp, str) or not timestamp:
        raise SlackError("Slack chat.postMessage 응답에 메시지 ts가 없습니다.")
    _call_slack_api(
        client,
        "pins.add",
        bot_token,
        {"channel": channel_id, "timestamp": timestamp},
        timeout,
        max_attempts,
    )
