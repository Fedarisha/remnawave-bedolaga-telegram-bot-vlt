"""Regression tests for subscription service HWID preservation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.external.remnawave_api import TrafficLimitStrategy, UserStatus
from app.services.subscription_service import SubscriptionService


def _panel_user(uuid: str = 'panel-uuid') -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        uuid=uuid,
        short_uuid='short',
        subscription_url='https://sub.example/short',
        happ_crypto_link=None,
        status=UserStatus.ACTIVE,
        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=329,
        full_name='Svetlana',
        username=None,
        telegram_id=837601435,
        email='Sas-01-72@yandex.ru',
        status='active',
        remnawave_id=101,
        remnawave_uuid='panel-uuid',
    )


def _subscription() -> SimpleNamespace:
    return SimpleNamespace(
        id=297,
        remnawave_id=101,
        remnawave_uuid='panel-uuid',
        actual_status='active',
        end_date=datetime(2026, 7, 11, tzinfo=UTC),
        traffic_limit_gb=100,
        tariff=None,
        connected_squads=None,
    )


@pytest.mark.asyncio
async def test_multi_create_or_update_existing_user_preserves_hwid_devices() -> None:
    service = SubscriptionService()
    existing = _panel_user()
    api = SimpleNamespace(
        get_user_by_uuid=AsyncMock(return_value=existing),
        get_user_by_id=AsyncMock(return_value=existing),
        update_user=AsyncMock(return_value=existing),
        reset_user_devices=AsyncMock(),
    )

    result = await service._create_or_update_remnawave_user_multi(
        api,
        _user(),
        _subscription(),
        user_tag=None,
        hwid_limit=1,
        ext_squad_uuid=None,
        reset_traffic=False,
        reset_reason=None,
    )

    assert result is existing
    api.update_user.assert_awaited_once()
    api.reset_user_devices.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_create_or_update_existing_user_preserves_hwid_devices() -> None:
    service = SubscriptionService()
    existing = _panel_user()
    api = SimpleNamespace(
        get_user_by_uuid=AsyncMock(return_value=existing),
        get_user_by_id=AsyncMock(return_value=existing),
        get_user_by_telegram_id=AsyncMock(return_value=[]),
        get_user_by_email=AsyncMock(return_value=[]),
        update_user=AsyncMock(return_value=existing),
        reset_user_devices=AsyncMock(),
    )

    result = await service._create_or_update_remnawave_user_single(
        api,
        _user(),
        _subscription(),
        user_tag=None,
        hwid_limit=1,
        ext_squad_uuid=None,
        reset_traffic=False,
        reset_reason=None,
    )

    assert result is existing
    api.update_user.assert_awaited_once()
    api.reset_user_devices.assert_not_awaited()
