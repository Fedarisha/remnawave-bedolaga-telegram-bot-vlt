"""Очередь ручной проверки чеков NaloGO: чек, созданный админом, обязан дойти
до покупателя.

Ручная пересылка из админки («🔄 Отправить») была единственной веткой создания
чека без доставки: чек уходил в ФНС, из очереди удалялся, а покупатель не
получал его ни по одному каналу. Плюс адрес почты не сохранялся в очередь, так
что к моменту пересылки его было уже неоткуда взять.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.services.nalogo_receipt_delivery as delivery_module
import app.services.nalogo_service as nalogo_module
from app.services.nalogo_service import NALOGO_PENDING_VERIFICATION_KEY, NaloGoService


class _FakeCache:
    """Минимальный in-memory дублёр списков app.utils.cache."""

    def __init__(self, lists=None):
        self.lists = {k: list(v) for k, v in (lists or {}).items()}
        self.values = {}

    async def lrange(self, key):
        return list(self.lists.get(key, []))

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return True

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def delete(self, key):
        self.lists.pop(key, None)
        self.values.pop(key, None)
        return True

    async def set(self, key, value, expire=None):
        self.values[key] = value
        return True


def _service() -> NaloGoService:
    service = NaloGoService.__new__(NaloGoService)
    service.configured = True
    service.client = SimpleNamespace(base_url='https://lknpd.nalog.ru/api/')
    service.inn = '123456789012'
    return service


def _pending(**overrides) -> dict:
    base = {
        'name': 'Оплата подписки',
        'amount': 1499.0,
        'quantity': 1,
        'client_info': None,
        'payment_id': 'pay-1',
        'telegram_user_id': 111,
        'amount_kopeks': 149900,
        'receipt_delivery_email': 'buyer@example.com',
        'receipt_delivery_language': 'ru',
        'status': 'pending_verification',
    }
    return base | overrides


async def test_pending_verification_entry_keeps_delivery_contact(monkeypatch):
    """Контакт покупателя обязан лечь в очередь — иначе при пересылке его нет."""
    cache = _FakeCache()
    monkeypatch.setattr(nalogo_module, 'cache', cache)

    await _service()._save_pending_verification(
        name='Оплата подписки',
        amount=1499.0,
        quantity=1,
        client_info=None,
        payment_id='pay-1',
        telegram_user_id=None,
        amount_kopeks=149900,
        receipt_delivery_email='buyer@example.com',
        receipt_delivery_language='ru',
        error_message='timeout',
    )

    (stored,) = cache.lists[NALOGO_PENDING_VERIFICATION_KEY]
    assert stored['receipt_delivery_email'] == 'buyer@example.com'
    assert stored['receipt_delivery_language'] == 'ru'


async def test_retry_delivers_receipt_to_the_buyer(monkeypatch):
    """Чек, созданный ручной пересылкой, доставляется покупателю."""
    cache = _FakeCache({NALOGO_PENDING_VERIFICATION_KEY: [_pending()]})
    monkeypatch.setattr(nalogo_module, 'cache', cache)
    notify = AsyncMock()
    monkeypatch.setattr(delivery_module, 'deliver_nalogo_receipt', notify)
    service = _service()
    monkeypatch.setattr(service, 'create_receipt', AsyncMock(return_value='uuid-42'))
    bot = SimpleNamespace()

    assert await service.retry_pending_receipt('pay-1', bot=bot) == 'uuid-42'

    notify.assert_awaited_once()
    kwargs = notify.await_args.kwargs
    assert kwargs['receipt_uuid'] == 'uuid-42'
    assert kwargs['telegram_id'] == 111
    assert kwargs['email'] == 'buyer@example.com'
    assert kwargs['language'] == 'ru'
    # чек ушёл из очереди проверки — повторно его не пришлют
    assert cache.lists.get(NALOGO_PENDING_VERIFICATION_KEY, []) == []


async def test_retry_delivers_legacy_entry_without_amount_kopeks(monkeypatch):
    """Старые записи очереди без amount_kopeks всё равно доставляются покупателю."""
    cache = _FakeCache({NALOGO_PENDING_VERIFICATION_KEY: [_pending(amount_kopeks=None)]})
    monkeypatch.setattr(nalogo_module, 'cache', cache)
    notify = AsyncMock()
    monkeypatch.setattr(delivery_module, 'deliver_nalogo_receipt', notify)
    service = _service()
    monkeypatch.setattr(service, 'create_receipt', AsyncMock(return_value='uuid-42'))

    await service.retry_pending_receipt('pay-1', bot=SimpleNamespace())

    notify.assert_awaited_once()
    assert notify.await_args.kwargs['email'] == 'buyer@example.com'


async def test_retry_keeps_receipt_when_delivery_fails(monkeypatch):
    """Доставка упала — чек в ФНС уже создан, из очереди он всё равно уходит.

    Иначе админ увидит ошибку, нажмёт «повторить» и выпишет второй чек на тот
    же платёж.
    """
    cache = _FakeCache({NALOGO_PENDING_VERIFICATION_KEY: [_pending()]})
    monkeypatch.setattr(nalogo_module, 'cache', cache)
    monkeypatch.setattr(
        delivery_module,
        'deliver_nalogo_receipt',
        AsyncMock(side_effect=RuntimeError('telegram down')),
    )
    service = _service()
    monkeypatch.setattr(service, 'create_receipt', AsyncMock(return_value='uuid-42'))

    assert await service.retry_pending_receipt('pay-1', bot=SimpleNamespace()) == 'uuid-42'
    assert cache.lists.get(NALOGO_PENDING_VERIFICATION_KEY, []) == []


async def test_retry_without_bot_still_delivers_receipt(monkeypatch):
    """Без bot чек создаётся и всё равно доставляется — доставка поднимет бота сама."""
    cache = _FakeCache({NALOGO_PENDING_VERIFICATION_KEY: [_pending()]})
    monkeypatch.setattr(nalogo_module, 'cache', cache)
    notify = AsyncMock()
    monkeypatch.setattr(delivery_module, 'deliver_nalogo_receipt', notify)
    service = _service()
    monkeypatch.setattr(service, 'create_receipt', AsyncMock(return_value='uuid-42'))

    assert await service.retry_pending_receipt('pay-1') == 'uuid-42'
    notify.assert_awaited_once()
