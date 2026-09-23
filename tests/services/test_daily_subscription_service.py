from types import SimpleNamespace

import pytest

from app.services import daily_subscription_service as daily_service_module
from app.services.daily_subscription_service import DailySubscriptionService


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


class ExplodingSubscription:
    def __init__(self) -> None:
        self.id = 101
        self.user_id = 202
        self.tariff_id = 303

    @property
    def user(self):
        if 'user' in self.__dict__:
            return self.__dict__['user']
        raise AssertionError('lazy user relationship was accessed')

    @user.setter
    def user(self, value) -> None:
        self.__dict__['user'] = value

    @property
    def tariff(self):
        if 'tariff' in self.__dict__:
            return self.__dict__['tariff']
        raise AssertionError('lazy tariff relationship was accessed')

    @tariff.setter
    def tariff(self, value) -> None:
        self.__dict__['tariff'] = value


@pytest.mark.anyio('asyncio')
async def test_process_single_charge_avoids_lazy_relationship_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DailySubscriptionService()
    subscription = ExplodingSubscription()
    user = SimpleNamespace(id=subscription.user_id)
    tariff = SimpleNamespace(id=subscription.tariff_id, daily_price_kopeks=0)

    async def fake_get_user_by_id(db, user_id):
        assert user_id == subscription.user_id
        return user

    async def fake_get_tariff_by_id(db, tariff_id, *, with_promo_groups=True):
        assert tariff_id == subscription.tariff_id
        assert with_promo_groups is False
        return tariff

    monkeypatch.setattr(
        daily_service_module,
        'sa_inspect',
        lambda obj: SimpleNamespace(dict=obj.__dict__),
    )
    monkeypatch.setattr(daily_service_module, 'get_user_by_id', fake_get_user_by_id)
    monkeypatch.setattr(daily_service_module, 'get_tariff_by_id', fake_get_tariff_by_id)

    result = await service._process_single_charge(SimpleNamespace(), subscription)

    assert result == 'error'
    assert subscription.user is user
    assert subscription.tariff is tariff


@pytest.mark.anyio('asyncio')
async def test_notify_daily_charge_skips_unloaded_tariff_without_lazy_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DailySubscriptionService()
    service._bot = object()
    subscription = ExplodingSubscription()
    user = SimpleNamespace(language='ru', balance_kopeks=1450)
    captured_message: dict[str, str] = {}

    async def fake_notify_daily_debit(**kwargs):
        captured_message['telegram_message'] = kwargs['telegram_message']

    monkeypatch.setattr(
        daily_service_module,
        'sa_inspect',
        lambda obj: SimpleNamespace(dict=obj.__dict__),
    )
    monkeypatch.setattr(
        daily_service_module.notification_delivery_service,
        'notify_daily_debit',
        fake_notify_daily_debit,
    )

    await service._notify_daily_charge(user, subscription, 500)

    assert 'Тариф:' not in captured_message['telegram_message']


@pytest.mark.anyio('asyncio')
async def test_process_single_charge_notifies_once_per_situation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DailySubscriptionService()
    service._bot = object()

    fake_cache = {}

    class FakeCache:
        async def get(self, key):
            return fake_cache.get(key)

        async def set(self, key, val, expire=None):
            fake_cache[key] = val

        async def delete(self, key):
            fake_cache.pop(key, None)

    monkeypatch.setattr('app.utils.cache.cache', FakeCache())

    # Subscription is active initially, daily price 1200, user has 400
    user = SimpleNamespace(id=202, balance_kopeks=400, language='ru')
    tariff = SimpleNamespace(id=303, daily_price_kopeks=1200, name='Суточный')
    subscription = SimpleNamespace(
        id=101,
        user_id=202,
        tariff_id=303,
        status='active',
        is_daily_paused=False,
    )

    notifications_sent = []

    async def fake_suspend(db, sub):
        sub.status = 'disabled'

    async def fake_notify(u, sub, price):
        notifications_sent.append((u.id, sub.id, price))

    async def fake_lock_user(db, uid):
        return user

    async def fake_load_context(db, sub):
        return user, tariff

    monkeypatch.setattr('app.database.crud.user.lock_user_for_pricing', fake_lock_user)
    monkeypatch.setattr(daily_service_module, 'suspend_daily_subscription_insufficient_balance', fake_suspend)
    monkeypatch.setattr(service, '_load_subscription_context', fake_load_context)
    monkeypatch.setattr(service, '_notify_insufficient_balance', fake_notify)

    # 1. First charge attempt: ACTIVE -> DISABLED, notification sent
    res1 = await service._process_single_charge(SimpleNamespace(), subscription)
    assert res1 == 'suspended'
    assert len(notifications_sent) == 1
    assert fake_cache.get('daily_insuf_notify:101') == '1'

    # 2. Second charge attempt without resolution (status now disabled, cache key present)
    res2 = await service._process_single_charge(SimpleNamespace(), subscription)
    assert res2 == 'suspended'
    assert len(notifications_sent) == 1  # Still 1, NOT sent again!

    # 3. Third charge attempt even if status somehow checked again, cache key prevents duplicate
    res3 = await service._process_single_charge(SimpleNamespace(), subscription)
    assert res3 == 'suspended'
    assert len(notifications_sent) == 1  # Still 1!


@pytest.mark.anyio('asyncio')
async def test_process_auto_resume_skips_when_insufficient_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DailySubscriptionService()

    user = SimpleNamespace(id=202, balance_kopeks=400, language='ru')
    tariff = SimpleNamespace(id=303, daily_price_kopeks=1200, name='Суточный')
    subscription = SimpleNamespace(
        id=101,
        user_id=202,
        tariff_id=303,
        status='disabled',
        is_daily_paused=False,
        end_date=None,
    )

    charges_attempted = []

    async def fake_process_charge(db, sub):
        charges_attempted.append(sub.id)
        return 'suspended'

    async def fake_load_context(db, sub):
        return user, tariff

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(daily_service_module, 'AsyncSessionLocal', lambda: FakeSession())
    monkeypatch.setattr(
        daily_service_module,
        'get_disabled_daily_subscriptions_for_resume',
        lambda db: [subscription],
    )
    monkeypatch.setattr(
        daily_service_module,
        'get_expired_daily_subscriptions_for_recovery',
        lambda db: [],
    )
    monkeypatch.setattr(daily_service_module, 'has_unexpired_paid_time', lambda sub: False)
    monkeypatch.setattr(service, '_load_subscription_context', fake_load_context)
    monkeypatch.setattr(service, '_process_single_charge', fake_process_charge)

    # User only has 400 kopeks, tariff is 1200 kopeks
    stats = await service.process_auto_resume()

    assert stats['resumed'] == 0
    assert len(charges_attempted) == 0
    assert subscription.status == 'disabled'  # Status remains disabled, NO flapping!


