"""Regression tests for daily-tariff pricing on landing/guest purchases.

Background — the bug (Telegram report #543696)
----------------------------------------------
Landing pages that sold a *daily* tariff (``is_daily=True``) showed a price
of 0 and could not be purchased. Daily tariffs keep their price in
``daily_price_kopeks`` and leave ``period_prices`` empty, but the landing
config loader and the guest-purchase validator only ever read
``period_prices`` (via ``get_price_for_period`` / ``get_available_periods``).
For a daily tariff that meant "no period, no price" → the tariff was dropped
or surfaced at price 0.

The fix routes both paths through the daily-aware model helper
``Tariff.get_effective_price``.

Fork divergence from upstream: a guest prepays N days of a daily tariff on the
landing (bounded 1..365) instead of upstream's single 1-day period — the rest is
auto-charged from balance by ``daily_subscription_service``. These tests pin the
fork contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.guest_purchase_service import GuestPurchaseError, validate_and_calculate


def _daily_tariff(tariff_id: int = 1, daily_price_kopeks: int = 5000) -> SimpleNamespace:
    """A stub daily tariff mirroring the real model's daily-aware helpers."""

    def get_price_for_period(days: int) -> int | None:
        return None  # daily tariffs keep no period_prices

    def get_available_periods() -> list[int]:
        return []

    def get_purchasable_periods() -> list[int]:
        return [1] if daily_price_kopeks else []

    def get_purchasable_price_for_period(days: int) -> int | None:
        if days == 1:
            return daily_price_kopeks or None
        return None

    def get_effective_price(days: int) -> int | None:
        # Форк: суточный тариф стоит daily_price_kopeks за каждый предоплаченный день.
        if not daily_price_kopeks or days <= 0:
            return None
        return daily_price_kopeks * days

    return SimpleNamespace(
        id=tariff_id,
        is_active=True,
        is_daily=True,
        daily_price_kopeks=daily_price_kopeks,
        get_price_for_period=get_price_for_period,
        get_available_periods=get_available_periods,
        get_purchasable_periods=get_purchasable_periods,
        get_purchasable_price_for_period=get_purchasable_price_for_period,
        get_effective_price=get_effective_price,
    )


def _landing(tariff_id: int = 1) -> SimpleNamespace:
    """A landing allowing one tariff, no period overrides, no discount."""
    return SimpleNamespace(
        id=10,
        allowed_tariff_ids=[tariff_id],
        allowed_periods={},
        discount_percent=0,
        discount_starts_at=None,
        discount_ends_at=None,
        discount_overrides={},
    )


@pytest.mark.asyncio
async def test_daily_tariff_one_day_is_priced_from_daily_price() -> None:
    """A 1-day purchase of a daily tariff must cost ``daily_price_kopeks``,
    not 0 and not be rejected (Telegram report #543696)."""
    tariff = _daily_tariff(daily_price_kopeks=5000)
    db = AsyncMock()

    with patch(
        'app.services.guest_purchase_service.get_tariff_by_id',
        AsyncMock(return_value=tariff),
    ):
        resolved, price = await validate_and_calculate(db, _landing(), tariff_id=1, period_days=1)

    assert resolved is tariff
    assert price == 5000, 'daily tariff must surface its real daily price, not 0'


@pytest.mark.asyncio
async def test_daily_tariff_prepay_is_priced_per_day() -> None:
    """Форк: гость предоплачивает N суток, цена — daily_price_kopeks × N."""
    tariff = _daily_tariff(daily_price_kopeks=5000)
    db = AsyncMock()

    with patch(
        'app.services.guest_purchase_service.get_tariff_by_id',
        AsyncMock(return_value=tariff),
    ):
        _resolved, price = await validate_and_calculate(db, _landing(), tariff_id=1, period_days=30)

    assert price == 5000 * 30


@pytest.mark.asyncio
@pytest.mark.parametrize('period_days', [0, 366])
async def test_daily_tariff_rejects_period_outside_prepay_window(period_days: int) -> None:
    """Окно предоплаты ограничено 1..365 сутками — вне его покупка отклоняется."""
    tariff = _daily_tariff(daily_price_kopeks=5000)
    db = AsyncMock()

    with patch(
        'app.services.guest_purchase_service.get_tariff_by_id',
        AsyncMock(return_value=tariff),
    ):
        with pytest.raises(GuestPurchaseError):
            await validate_and_calculate(db, _landing(), tariff_id=1, period_days=period_days)
