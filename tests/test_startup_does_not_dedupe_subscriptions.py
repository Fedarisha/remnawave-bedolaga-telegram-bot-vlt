"""Форк не схлопывает «дубли» тарифных подписок при старте.

Апстрим гоняет ``dedupe_expired_tariff_subscriptions`` фоном на каждом запуске:
у него (user_id, tariff_id) уникален по частичному индексу, поэтому несколько
подписок на один тариф — накопившийся мусор, который надо удалить.

Здесь этот индекс снят намеренно (миграция 0059): повторная покупка того же
тарифа создаёт ОТДЕЛЬНУЮ подписку и является штатным сценарием, а истёкшие
строки — история покупок, а не дубли. Автоматическое безвозвратное удаление на
старте недопустимо, поэтому вызов из main.py убран; сама функция оставлена и
может быть вызвана вручную.

Тест держит это решение: очередной мёрж апстрима легко вернёт вызов обратно, и
заметить это по диффу из сотен файлов практически невозможно.
"""

from __future__ import annotations

import ast
from pathlib import Path


MAIN_PATH = Path(__file__).resolve().parents[1] / 'main.py'


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_startup_never_calls_the_subscription_dedupe() -> None:
    tree = ast.parse(MAIN_PATH.read_text(encoding='utf-8'))

    assert 'dedupe_expired_tariff_subscriptions' not in _called_names(tree), (
        'main.py must not run dedupe_expired_tariff_subscriptions on startup: in this fork a user '
        'may legitimately own several subscriptions of the same tariff, so collapsing the expired '
        'ones deletes purchase history irreversibly on first boot'
    )


def test_the_dedupe_function_is_still_available_for_manual_use() -> None:
    """Отключён автозапуск, а не сама возможность — иначе это была бы потеря функции."""
    from app.services.subscription_dedup_service import dedupe_expired_tariff_subscriptions

    assert callable(dedupe_expired_tariff_subscriptions)
