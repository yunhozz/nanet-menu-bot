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
