from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.cabinet.routes.x_ui_migration as migration_route


async def test_admin_migration_restores_subscription_for_selected_user(monkeypatch):
    db = SimpleNamespace()
    admin = SimpleNamespace(id=7)
    user = SimpleNamespace(id=42)
    expires_at = datetime.now(UTC) + timedelta(days=30)
    subscription = SimpleNamespace(id=101, end_date=expires_at, days_left=30)
    tariff = SimpleNamespace(id=3, name='Archive')
    migration_result = SimpleNamespace(
        subscription=subscription,
        tariff=tariff,
        apology_days=2,
        was_unlimited=False,
    )

    monkeypatch.setattr(type(migration_route.settings), 'is_x_ui_migration_enabled', lambda self: True, raising=False)
    get_user_mock = AsyncMock(return_value=user)
    migrate_mock = AsyncMock(return_value=migration_result)
    monkeypatch.setattr(migration_route, 'get_user_by_id', get_user_mock)
    monkeypatch.setattr(migration_route, 'migrate_vless_subscription', migrate_mock)

    response = await migration_route.admin_migrate_user_subscription(
        user_id=user.id,
        request=migration_route.XUiMigrateRequest(link='  legacy-uuid  '),
        admin=admin,
        db=db,
    )

    get_user_mock.assert_awaited_once_with(db, user.id)
    migrate_mock.assert_awaited_once_with(db, user, 'legacy-uuid')
    assert response.success is True
    assert response.subscription_id == subscription.id
    assert response.tariff_id == tariff.id
    assert response.days_left == 30


async def test_admin_migration_rejects_unknown_user(monkeypatch):
    monkeypatch.setattr(type(migration_route.settings), 'is_x_ui_migration_enabled', lambda self: True, raising=False)
    monkeypatch.setattr(migration_route, 'get_user_by_id', AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as error:
        await migration_route.admin_migrate_user_subscription(
            user_id=999,
            request=migration_route.XUiMigrateRequest(link='legacy-uuid'),
            admin=SimpleNamespace(id=7),
            db=SimpleNamespace(),
        )

    assert error.value.status_code == 404
    assert error.value.detail == 'User not found'
