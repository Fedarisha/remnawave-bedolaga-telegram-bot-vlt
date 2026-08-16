"""Source-level pin: cabinet ``/purchase-tariff`` must resolve an EXPIRED
trial of the SAME tariff when renewing, so it converts the trial in place
(same Remnawave user/link) instead of spawning a new subscription + new link.

Background — the bug this defends against
-----------------------------------------
Prod report (2026-06): a user whose 3-day trial has EXPIRED renews the same
("Базовый") tariff via the cabinet. ``purchase_tariff`` resolved the existing
subscription via ``get_subscription_by_user_and_tariff(user, tariff)`` WITHOUT
``include_inactive`` → that lookup only matches ACTIVE/TRIAL/LIMITED, so the
expired trial (``status='expired'``) was invisible → the trial got killed by
``deactivate_user_trial_subscriptions`` and a fresh ``create_paid_subscription``
ran → a NEW Remnawave user + NEW subscription link → the user has to re-add all
devices. 7 affected users on prod.

Fix shape
---------
Pass ``include_inactive=True`` to the tariff-level lookup so an EXPIRED (or
disabled) same-tariff subscription is found. It then flows into the existing
extend-in-place branch (``extend_subscription`` clears the trial flag) and
``update_remnawave_user`` (the row already carries ``remnawave_id``) → SAME
link. Picking a DIFFERENT tariff still returns ``None`` for that tariff → a new
subscription, which is the intended "same-tariff only" semantic.

A full integration test would need a real DB + FastAPI deps; this pins the
SOURCE-LEVEL contract — the bug class ("drop include_inactive") is grep-detectable.
"""

from __future__ import annotations

import ast
from pathlib import Path


PURCHASE_PATH = (
    Path(__file__).resolve().parents[2] / 'app' / 'cabinet' / 'routes' / 'subscription_modules' / 'purchase.py'
)


def _find_async_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f'async function {name!r} not found in cabinet purchase.py')


def _function_source(source: str, func: ast.AsyncFunctionDef) -> str:
    lines = source.splitlines(keepends=True)
    end_line = func.end_lineno or len(lines)
    return ''.join(lines[func.lineno - 1 : end_line])


def test_purchase_tariff_has_no_tariff_level_fallback() -> None:
    """Форк-контракт: покупка тарифа в кабинете НЕ ищет существующую подписку по
    (user, tariff).

    В мульти-тарифе один и тот же тариф можно купить дважды — это отдельные
    подписки. Продление приходит с явным ``subscription_id``. Возврат
    тариф-левелового фоллбека молча превратил бы повторную покупку в продление.

    Конверсию истёкшего триала того же тарифа (чтобы не плодить нового
    Remnawave-юзера и новую ссылку) закрывает не фоллбек, а
    ``resolve_trial_conversion_candidate`` внутри ``create_paid_subscription``.
    """
    source = PURCHASE_PATH.read_text(encoding='utf-8')
    tree = ast.parse(source)
    func = _find_async_function(tree, 'purchase_tariff')
    body = _function_source(source, func)

    assert 'get_subscription_by_user_and_tariff(' not in body, (
        'purchase_tariff must not resolve an existing subscription by (user, tariff): '
        'in this fork a tariff purchase creates a new subscription unless the client '
        'pins subscription_id'
    )
    assert 'request.subscription_id' in body, (
        'purchase_tariff must honour the explicit subscription_id pin used by the renew flow'
    )
    assert 'resolve_trial_conversion_candidate' in body, (
        'expired same-tariff trials must still be converted in place via '
        'resolve_trial_conversion_candidate, otherwise the user gets a new Remnawave link'
    )
