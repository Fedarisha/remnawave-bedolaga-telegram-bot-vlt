"""Форк: синхронизация с панелью не гасит приостановленную суточную подписку.

DailySubscriptionService приостанавливает суточную подписку при нехватке баланса
(DISABLED) и сам возобновляет её после пополнения. Панель тем временем переводит
аккаунт в EXPIRED по прошедшей дате; если перенести это в бота, подписка выпадает
из выборки возобновления (там только DISABLED).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.database.models import SubscriptionStatus
from app.services.panel_sync.projection import _is_suspended_daily


NOW = datetime(2026, 10, 21, tzinfo=UTC)


def _sub(status, *, tariff=None, last_charge=None):
    sub = SimpleNamespace(status=status, last_daily_charge_at=last_charge, end_date=NOW - timedelta(days=3))
    if tariff is not None:
        sub.tariff = tariff
    return sub


def test_disabled_daily_by_loaded_tariff_is_suspended():
    assert _is_suspended_daily(_sub(SubscriptionStatus.DISABLED.value, tariff=SimpleNamespace(is_daily=True)))


def test_disabled_without_loaded_tariff_falls_back_to_charge_trace():
    assert _is_suspended_daily(_sub(SubscriptionStatus.DISABLED.value, last_charge=NOW - timedelta(days=4)))
    assert not _is_suspended_daily(_sub(SubscriptionStatus.DISABLED.value))


def test_loaded_non_daily_tariff_wins_over_charge_trace():
    # Ушёл с суточного на обычный тариф: след списаний остался, но это не суточная.
    sub = _sub(
        SubscriptionStatus.DISABLED.value,
        tariff=SimpleNamespace(is_daily=False),
        last_charge=NOW - timedelta(days=30),
    )
    assert not _is_suspended_daily(sub)


def test_only_disabled_counts():
    for status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.EXPIRED.value):
        assert not _is_suspended_daily(_sub(status, tariff=SimpleNamespace(is_daily=True)))
