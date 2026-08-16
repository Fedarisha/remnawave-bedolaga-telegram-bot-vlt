"""Синк из панели не имеет права создавать подписки до бэкфила числовых id.

Инцидент (мёрж upstream v4.0.0, 2026-08-16): после запуска пользователям
дополнительно выдалась суточная подписка.

Механика: Remnawave 3.0.0 перевёл панельную идентичность на числовой
``remnawave_id``, и дедупликация в ``_sync_users_from_panel_multi`` стала
сравнивать ``s.remnawave_id == panel_user_id`` вместо прежнего сравнения по
uuid. Миграция 0104 добавляет колонку NULLable и НЕ заполняет её — заполняет
отдельный ручной бэкфил. До него ``remnawave_id`` равен NULL у всех
доапгрейдных подписок, поэтому проверка «у этого юзера уже есть подписка с
этим панельным id» не срабатывала никогда: синк заводил вторую подписку
каждому пользователю панели, а тариф подбирался по совпадению squad'ов — в
этой инсталляции им оказался суточный, и DailySubscriptionService начинал
списывать за него ежедневно.

Апстрим от дубля прикрыт частичным UNIQUE (user_id, tariff_id); форк его
намеренно снял (миграция 0059), поэтому предохранитель нужен в коде.
"""

from __future__ import annotations

import ast
import contextlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, Subscription
from app.services.remnawave_identity_backfill import (
    legacy_identity_backfill_pending,
    subscriptions_carry_unbackfilled_identity,
)


SERVICE_PATH = Path(__file__).resolve().parents[2] / 'app' / 'services' / 'remnawave_service.py'


def _ensure_real_aiosqlite(monkeypatch):
    stub = sys.modules.get('aiosqlite')
    if stub is not None and not hasattr(stub, 'connect'):
        monkeypatch.delitem(sys.modules, 'aiosqlite', raising=False)


@contextlib.asynccontextmanager
async def _memory_session(monkeypatch):
    _ensure_real_aiosqlite(monkeypatch)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[Subscription.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


def _sub(**overrides) -> Subscription:
    base = dict(
        user_id=1,
        status='active',
        end_date=datetime.now(UTC) + timedelta(days=30),
        remnawave_id=None,
        remnawave_uuid=None,
    )
    base.update(overrides)
    return Subscription(**base)


async def test_pending_while_a_legacy_row_has_no_numeric_id(monkeypatch):
    """Строка с легаси-uuid и без числового id — бэкфил не выполнен."""
    async with _memory_session(monkeypatch) as session:
        session.add(_sub(remnawave_uuid='11111111-2222-3333-4444-555555555555'))
        await session.commit()

        assert await legacy_identity_backfill_pending(session) is True


async def test_not_pending_once_numeric_ids_are_filled(monkeypatch):
    """У всех легаси-строк проставлен числовой id — создавать подписки снова безопасно."""
    async with _memory_session(monkeypatch) as session:
        session.add(_sub(remnawave_uuid='11111111-2222-3333-4444-555555555555', remnawave_id=42))
        await session.commit()

        assert await legacy_identity_backfill_pending(session) is False


async def test_not_pending_on_a_clean_install(monkeypatch):
    """Свежая база без легаси-идентичности не должна блокировать синк."""
    async with _memory_session(monkeypatch) as session:
        session.add(_sub())
        await session.commit()

        assert await legacy_identity_backfill_pending(session) is False


def test_per_user_gate_only_blocks_users_with_legacy_rows():
    """Блокируем создание точечно: пользователь с восстановленной идентичностью не страдает."""
    legacy = _sub(remnawave_uuid='11111111-2222-3333-4444-555555555555')
    resolved = _sub(remnawave_uuid='11111111-2222-3333-4444-555555555555', remnawave_id=42)
    clean = _sub()

    assert subscriptions_carry_unbackfilled_identity([legacy]) is True
    assert subscriptions_carry_unbackfilled_identity([resolved]) is False
    assert subscriptions_carry_unbackfilled_identity([clean]) is False
    assert subscriptions_carry_unbackfilled_identity([resolved, legacy]) is True
    assert subscriptions_carry_unbackfilled_identity([]) is False


def _sync_function_source() -> str:
    source = SERVICE_PATH.read_text(encoding='utf-8')
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == '_sync_users_from_panel_multi':
            lines = source.splitlines(keepends=True)
            return ''.join(lines[node.lineno - 1 : node.end_lineno or len(lines)])
    raise AssertionError('_sync_users_from_panel_multi not found in remnawave_service.py')


def test_sync_checks_the_backfill_before_creating_subscriptions():
    """Предохранитель обязан стоять ВНУТРИ ветки создания, иначе дубли вернутся."""
    body = _sync_function_source()

    create_branch_idx = body.find('if not subscription:')
    create_idx = body.find('new_sub = Subscription(')
    assert create_branch_idx >= 0 and create_idx > create_branch_idx, 'creation branch not found — update this test'

    # Именно ВЫЗОВ (со скобкой), а не упоминание в блоке импорта: импорт стоит
    # выше по функции и «проходил» бы проверку даже после удаления гейта.
    gate_idx = body.find('subscriptions_carry_unbackfilled_identity(', create_branch_idx)
    assert 0 <= gate_idx < create_idx, (
        '_sync_users_from_panel_multi must call subscriptions_carry_unbackfilled_identity() inside the '
        'creation branch, before building the row: its dedup compares numeric remnawave_id, which is '
        'NULL on every pre-3.0.0 subscription until the backfill has run, so without this gate the sync '
        'hands every panel user a second subscription'
    )
    assert '_identity_backfill_pending' in body[create_branch_idx:create_idx], (
        'the per-run backfill flag must gate the create branch too'
    )
