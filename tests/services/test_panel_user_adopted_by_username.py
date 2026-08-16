"""Подписка без числового id И без shortUuid не должна плодить второй панельный аккаунт.

Remnawave 3.0.0 опознаёт пользователя только по числовому ``remnawave_id``,
который проставляет отдельный ручной бэкфил. До него строки живут с пустым id, и
в multi-tariff единственная страховка — адопция по ``remnawave_short_uuid``.
Если пуст и он (строка старше колонки, чистка стёрла его как «мусор», панель
когда-то ответила 404), поиска по telegramId в multi-tariff нет намеренно: у
человека несколько панельных аккаунтов с одним telegramId, и выбор был бы
наугад. Раньше в этом случае код шёл прямиком в ``create_user`` — оплаченный
аккаунт осиротевал, а пользователю менялась ссылка на подписку.

По username выбор однозначен: в multi-tariff он несёт per-subscription суффикс
``remnawave_short_id``. Тем же приёмом пользуется бэкфил.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.external.remnawave_api import TrafficLimitStrategy, UserStatus
from app.services.subscription_service import SubscriptionService


def _panel_user(panel_id: int = 777, telegram_id: int | None = 837601435) -> SimpleNamespace:
    return SimpleNamespace(
        id=panel_id,
        uuid='panel-uuid',
        short_uuid='short',
        subscription_url='https://sub.example/short',
        happ_crypto_link=None,
        status=UserStatus.ACTIVE,
        telegram_id=telegram_id,
        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=329,
        full_name='Svetlana',
        username=None,
        telegram_id=837601435,
        email='buyer@example.com',
        status='active',
        remnawave_id=None,
        remnawave_uuid='legacy-uuid',
    )


def _orphan_subscription() -> SimpleNamespace:
    """Ровно проблемная строка: панельной идентичности не осталось ни в каком виде."""
    return SimpleNamespace(
        id=297,
        remnawave_id=None,
        remnawave_short_uuid=None,
        remnawave_short_id='a1b2c3',
        actual_status='active',
        end_date=datetime.now(UTC) + timedelta(days=30),
        traffic_limit_gb=100,
        tariff=None,
        connected_squads=None,
    )


def _api(*, by_username) -> SimpleNamespace:
    existing = _panel_user()
    return SimpleNamespace(
        get_user_by_id=AsyncMock(return_value=existing),
        get_user_by_short_uuid=AsyncMock(return_value=None),
        get_user_by_username=AsyncMock(return_value=by_username),
        update_user=AsyncMock(return_value=existing),
        create_user=AsyncMock(return_value=existing),
        reset_user_devices=AsyncMock(),
    )


async def _run(api, subscription):
    return await SubscriptionService()._create_or_update_remnawave_user_multi(
        api,
        _user(),
        subscription,
        user_tag=None,
        hwid_limit=1,
        ext_squad_uuid=None,
        reset_traffic=False,
        reset_reason=None,
    )


@pytest.mark.asyncio
async def test_existing_panel_user_is_adopted_instead_of_duplicated():
    existing = _panel_user()
    api = _api(by_username=existing)
    subscription = _orphan_subscription()

    await _run(api, subscription)

    api.create_user.assert_not_awaited()
    api.update_user.assert_awaited_once()
    assert api.update_user.await_args.kwargs['user_id'] == existing.id
    # Идентичность восстановлена на строке — следующий проход пойдёт коротким путём.
    assert subscription.remnawave_id == existing.id


@pytest.mark.asyncio
async def test_unknown_username_still_creates_a_new_panel_user():
    """Обычная новая подписка: в панели такого username нет — создаём, как и раньше."""
    api = _api(by_username=None)

    await _run(api, _orphan_subscription())

    api.create_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_username_owned_by_another_telegram_id_is_not_adopted():
    """Совпал username, но аккаунт чужой — привязывать нельзя, иначе угон подписки."""
    api = _api(by_username=_panel_user(panel_id=999, telegram_id=111111111))
    subscription = _orphan_subscription()

    await _run(api, subscription)

    api.create_user.assert_awaited_once()
    assert subscription.remnawave_id is None


@pytest.mark.asyncio
async def test_panel_error_is_not_swallowed_into_a_duplicate():
    """5xx/таймаут — это «не знаем», а не «аккаунта нет»: молча создавать дубль нельзя."""
    api = _api(by_username=None)
    api.get_user_by_username = AsyncMock(side_effect=RuntimeError('panel restarting'))

    with pytest.raises(RuntimeError):
        await _run(api, _orphan_subscription())

    api.create_user.assert_not_awaited()
