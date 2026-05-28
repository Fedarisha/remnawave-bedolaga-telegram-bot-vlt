from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.database.models import GuestPurchaseStatus
from app.services import guest_purchase_service


class _DummyScalars:
    def __init__(self, item):
        self._item = item

    def first(self):
        return self._item


class _DummyResult:
    def __init__(self, item):
        self._item = item

    def scalars(self):
        return _DummyScalars(self._item)


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_find_or_create_email_guest_user_generates_referral_code(monkeypatch):
    db = MagicMock()
    db.execute = AsyncMock(return_value=_DummyResult(None))
    db.flush = AsyncMock()
    db.begin_nested = MagicMock(return_value=_AsyncContext())
    db.add = MagicMock()

    monkeypatch.setattr(
        guest_purchase_service,
        '_get_or_create_default_promo_group',
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(
        guest_purchase_service,
        'create_unique_referral_code',
        AsyncMock(return_value='refGuest1'),
    )

    user, is_new_account = await guest_purchase_service._find_or_create_user(
        db,
        'email',
        'new-user@example.com',
    )

    assert is_new_account is True
    assert user.referral_code == 'refGuest1'
    db.add.assert_called_once_with(user)


async def test_fulfill_purchase_keeps_gift_pending_activation_for_existing_subscription_in_multi_mode(monkeypatch):
    monkeypatch.setattr(
        type(guest_purchase_service.settings),
        'is_multi_tariff_enabled',
        lambda self: True,
        raising=False,
    )

    purchase = SimpleNamespace(
        id=1,
        token='gift-token',
        status=GuestPurchaseStatus.PAID.value,
        tariff_id=7,
        period_days=30,
        is_gift=True,
        gift_recipient_type='email',
        gift_recipient_value='user@example.com',
        cabinet_password=None,
        buyer=None,
        landing=None,
        user=None,
    )
    user = SimpleNamespace(id=10, language='ru', auth_type='telegram')
    tariff = SimpleNamespace(id=7, name='Gift Pro', get_effective_price=lambda period_days: 10_000)
    active_subscription = SimpleNamespace(id=55, is_active=True)

    db = MagicMock()
    db.execute = AsyncMock(return_value=_DummyResult(purchase))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr(guest_purchase_service, '_find_or_create_user', AsyncMock(return_value=(user, False)))
    monkeypatch.setattr(guest_purchase_service, 'get_tariff_by_id', AsyncMock(return_value=tariff))
    monkeypatch.setattr(
        'app.database.crud.subscription.get_active_subscriptions_by_user_id',
        AsyncMock(return_value=[active_subscription]),
    )

    notify_mock = AsyncMock()
    admin_notify_mock = AsyncMock()
    nalogo_mock = AsyncMock()
    create_paid_mock = AsyncMock()

    monkeypatch.setattr(guest_purchase_service, 'send_guest_notification', notify_mock)
    monkeypatch.setattr(guest_purchase_service, '_send_admin_notification', admin_notify_mock)
    monkeypatch.setattr(guest_purchase_service, '_create_nalogo_receipt_for_purchase', nalogo_mock)
    monkeypatch.setattr(guest_purchase_service, 'create_paid_subscription', create_paid_mock)

    result = await guest_purchase_service.fulfill_purchase(db, purchase.token)

    assert result is purchase
    assert purchase.status == GuestPurchaseStatus.PENDING_ACTIVATION.value
    assert purchase.user_id == user.id
    create_paid_mock.assert_not_awaited()
    notify_mock.assert_awaited_once()
    assert notify_mock.await_args.kwargs['is_pending_activation'] is True
    admin_notify_mock.assert_awaited_once()
    nalogo_mock.assert_awaited_once()


async def test_fulfill_daily_purchase_marks_initial_daily_charge(monkeypatch):
    monkeypatch.setattr(
        type(guest_purchase_service.settings),
        'is_multi_tariff_enabled',
        lambda self: False,
        raising=False,
    )

    purchase = SimpleNamespace(
        id=7,
        token='daily-token',
        status=GuestPurchaseStatus.PAID.value,
        tariff_id=2,
        period_days=7,
        amount_kopeks=8400,
        payment_method='yookassa',
        payment_id='payment-id',
        is_gift=False,
        contact_type='email',
        contact_value='dima_petru92@mail.ru',
        gift_recipient_type=None,
        gift_recipient_value=None,
        cabinet_password=None,
        buyer=None,
        landing=None,
        user=None,
        subid=None,
        subscription_url=None,
        subscription_crypto_link=None,
        receipt_uuid=None,
    )
    user = SimpleNamespace(
        id=237,
        language='ru',
        auth_type='telegram',
    )
    tariff = SimpleNamespace(
        id=2,
        name='Суточный',
        is_daily=True,
        allowed_squads=['squad-a'],
        traffic_limit_gb=100,
        device_limit=1,
        get_effective_price=lambda period_days: 8400 if period_days == 7 else None,
    )
    subscription = SimpleNamespace(
        id=234,
        end_date=datetime.now(UTC) + timedelta(days=7),
        subscription_url='https://sub.example/daily',
        subscription_crypto_link='vless://daily',
        last_daily_charge_at=None,
        is_daily_paused=True,
    )

    db = MagicMock()
    db.execute = AsyncMock(return_value=_DummyResult(purchase))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    service_mock = MagicMock()
    service_mock.create_remnawave_user = AsyncMock()

    monkeypatch.setattr(guest_purchase_service, '_find_or_create_user', AsyncMock(return_value=(user, False)))
    monkeypatch.setattr(guest_purchase_service, 'get_tariff_by_id', AsyncMock(return_value=tariff))
    monkeypatch.setattr(guest_purchase_service, 'get_subscription_by_user_id', AsyncMock(return_value=None))
    monkeypatch.setattr(guest_purchase_service, 'create_paid_subscription', AsyncMock(return_value=subscription))
    monkeypatch.setattr(guest_purchase_service, 'SubscriptionService', lambda: service_mock)
    monkeypatch.setattr(guest_purchase_service, 'create_transaction', AsyncMock())
    monkeypatch.setattr(guest_purchase_service, 'send_guest_notification', AsyncMock())
    monkeypatch.setattr(guest_purchase_service, '_send_admin_notification', AsyncMock())
    monkeypatch.setattr(guest_purchase_service, '_create_nalogo_receipt_for_purchase', AsyncMock())

    result = await guest_purchase_service.fulfill_purchase(db, purchase.token)

    assert result is purchase
    assert purchase.status == GuestPurchaseStatus.DELIVERED.value
    assert subscription.last_daily_charge_at is not None
    assert subscription.is_daily_paused is False
