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
    try:
        response = client.post(webhook_url, json=payload, timeout=timeout)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise SlackError(
            "Slack 전송 네트워크 오류", method="webhook", outcome_unknown=True
        ) from exc
    if response.status_code == 200 and response.text.strip() == "ok":
        return
    raise SlackError(
        f"Slack 전송 실패(status={response.status_code})",
        method="webhook",
        outcome_unknown=response.status_code >= 500,
    )


def _call_slack_api(
    client: requests.Session,
    method: str,
    token: str,
    payload: dict[str, Any],
    timeout: tuple[float, float],
    max_attempts: int,
) -> dict[str, Any]:
    max_attempts = max_attempts if method == "chat.update" else 1
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
                raise SlackError(
                    f"Slack {method} 호출이 네트워크 오류로 실패했습니다.",
                    method=method,
                    outcome_unknown=True,
                ) from exc
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
            raise SlackError(
                f"Slack {method} 실패(status={response.status_code})",
                method=method,
                outcome_unknown=response.status_code >= 500,
            )

        try:
            result = response.json()
        except requests.JSONDecodeError as exc:
            raise SlackError(
                f"Slack {method} 응답이 올바른 JSON이 아닙니다.",
                method=method,
                outcome_unknown=True,
            ) from exc
        if result.get("ok"):
            if method == "chat.postMessage" and (
                not isinstance(result.get("ts"), str) or not result["ts"]
            ):
                raise SlackError(
                    "Slack chat.postMessage 응답에 메시지 ts가 없습니다.",
                    method=method,
                    outcome_unknown=True,
                )
            return result
        error = result.get("error", "unknown")
        raise SlackError(
            f"Slack {method} 실패(error={error})",
            method=method,
            outcome_unknown=error in {"internal_error", "fatal_error", "unknown"},
        )

    raise AssertionError("unreachable")


def post_and_pin_to_slack(
    bot_token: str,
    channel_id: str,
    payload: SlackPayload,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 15.0),
    max_attempts: int = 3,
) -> str:
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
    try:
        _call_slack_api(
            client,
            "pins.add",
            bot_token,
            {"channel": channel_id, "timestamp": timestamp},
            timeout,
            max_attempts,
        )
    except SlackError as exc:
        raise SlackError(
            str(exc), method="pins.add", outcome_unknown=exc.outcome_unknown, message_ts=timestamp
        ) from exc
    return timestamp


def post_message_to_slack(
    bot_token: str,
    channel_id: str,
    payload: SlackPayload,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 15.0),
    max_attempts: int = 3,
) -> None:
    _call_slack_api(
        session or requests.Session(),
        "chat.postMessage",
        bot_token,
        {"channel": channel_id, **payload},
        timeout,
        max_attempts,
    )


def update_message_to_slack(
    bot_token: str,
    channel_id: str,
    timestamp: str,
    payload: dict[str, Any],
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 15.0),
    max_attempts: int = 3,
) -> None:
    _call_slack_api(
        session or requests.Session(),
        "chat.update",
        bot_token,
        {"channel": channel_id, "ts": timestamp, **payload},
        timeout,
        max_attempts,
    )
