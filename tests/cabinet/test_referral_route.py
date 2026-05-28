from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.cabinet.routes import referral as referral_routes


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


async def test_referral_info_backfills_missing_referral_code(monkeypatch):
    user = SimpleNamespace(
        id=237,
        referral_code=None,
        referral_commission_percent=None,
        balance_kopeks=0,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(0),
                _ScalarResult(0),
                _ScalarResult(0),
                _ScalarResult(0),
                _ScalarResult(0),
            ]
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    monkeypatch.setattr(
        referral_routes,
        'create_unique_referral_code',
        AsyncMock(return_value='refBackfill'),
    )

    response = await referral_routes.get_referral_info(user=user, db=db)

    assert user.referral_code == 'refBackfill'
    assert response.referral_code == 'refBackfill'
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(user)
