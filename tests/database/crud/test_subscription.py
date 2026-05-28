from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.database.crud import subscription as subscription_crud
from app.database.crud.subscription import create_trial_subscription


class _EmptyScalarResult:
    def all(self):
        return []


class _EmptyResult:
    def scalars(self):
        return _EmptyScalarResult()


async def test_get_daily_subscriptions_for_charge_waits_until_prepaid_period_ends():
    db = Mock()
    db.execute = AsyncMock(return_value=_EmptyResult())

    await subscription_crud.get_daily_subscriptions_for_charge(db)

    statement = str(db.execute.await_args.args[0])
    assert 'subscriptions.end_date <= :end_date_1' in statement


async def test_create_trial_subscription_uses_all_available_squads_by_default(monkeypatch):
    db = Mock()
    db.add = Mock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr('app.database.crud.subscription.get_subscription_by_user_id', AsyncMock(return_value=None))
    monkeypatch.setattr('app.database.crud.subscription.generate_unique_short_id', AsyncMock(return_value='abc123'))
    monkeypatch.setattr(
        'app.database.crud.server_squad.get_available_server_squads',
        AsyncMock(
            return_value=[
                SimpleNamespace(squad_uuid='fi-uuid'),
                SimpleNamespace(squad_uuid='ru-uuid'),
            ]
        ),
    )
    get_server_ids_mock = AsyncMock(return_value=[11, 12])
    add_user_to_servers_mock = AsyncMock()
    monkeypatch.setattr('app.database.crud.server_squad.get_server_ids_by_uuids', get_server_ids_mock)
    monkeypatch.setattr('app.database.crud.server_squad.add_user_to_servers', add_user_to_servers_mock)

    subscription = await create_trial_subscription(
        db,
        user_id=1,
        duration_days=14,
        traffic_limit_gb=100,
        device_limit=5,
    )

    assert subscription.connected_squads == ['fi-uuid', 'ru-uuid']
    db.add.assert_called_once_with(subscription)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(subscription)
    get_server_ids_mock.assert_awaited_once_with(db, ['fi-uuid', 'ru-uuid'])
    add_user_to_servers_mock.assert_awaited_once_with(db, [11, 12])
