from datetime import date

import pytest

from nanet_menu import firebase_function
from nanet_menu.errors import MenuParseError


def test_scheduled_function_posts_for_given_date(monkeypatch):
    target = date(2026, 7, 31)
    calls = []
    monkeypatch.setattr(
        firebase_function,
        "run",
        lambda delivery_date, *, dry_run: calls.append((delivery_date, dry_run)),
    )

    firebase_function._post_daily_menu(target)

    assert calls == [(target, False)]


def test_scheduled_function_skips_holiday_without_failure_alert(monkeypatch):
    target = date(2026, 1, 1)

    def fail_if_called(*args, **kwargs):
        pytest.fail("공휴일에는 메뉴 전송이나 실패 알림을 호출하면 안 됩니다.")

    monkeypatch.setattr(firebase_function, "run", fail_if_called)
    monkeypatch.setattr(firebase_function, "_post_failure_alert", fail_if_called)

    firebase_function._post_daily_menu(target)


def test_scheduled_function_reports_failure_and_reraises(monkeypatch):
    target = date(2026, 7, 31)
    error = MenuParseError("식단을 찾지 못했습니다.")
    alerts = []
    monkeypatch.setattr(
        firebase_function,
        "run",
        lambda delivery_date, *, dry_run: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        firebase_function,
        "_post_failure_alert",
        lambda delivery_date, caught: alerts.append((delivery_date, caught)),
    )

    with pytest.raises(MenuParseError, match="식단을 찾지 못했습니다"):
        firebase_function._post_daily_menu(target)

    assert alerts == [(target, error)]
