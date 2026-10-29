import asyncio

import pytest
from fastapi import HTTPException

from app.api import profile
from app.core import account


class FakeDB:
    def __init__(self, missing=()):
        self.missing = set(missing)
        self.deleted = []
        self.in_tx = False

    def transaction(self):
        db = self

        class Tx:
            async def __aenter__(self):
                db.in_tx = True

            async def __aexit__(self, *exc):
                db.in_tx = False
                return False
        return Tx()

    async def fetch_val(self, query, values=None):
        return values['name'].split('.', 1)[1] not in self.missing

    async def fetch_all(self, query, values=None):
        assert self.in_tx, 'every delete runs inside the one transaction'
        assert values == {'owner': 'u1'}
        self.deleted.append(query.split()[2])
        return [1]


def test_deletes_every_member_table_and_the_login_in_one_transaction():
    db = FakeDB(missing={'user_settings'})
    result = asyncio.run(account.delete_member(db, 'u1'))
    expected = [t for t, _ in account.MEMBER_TABLES if t != 'user_settings'] + ['auth.users']
    assert db.deleted == expected
    assert db.deleted[-1] == 'auth.users' and 'user_settings' not in result


def test_delete_needs_the_confirm_word_and_the_password(monkeypatch):
    user = {'sub': 'u1', 'email': 'a@b.c'}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(profile.delete_account(profile.DeleteAccountIn(password='x', confirm='nope'), user=user))
    assert exc.value.status_code == 400

    def bad_sign_in(email, password):
        raise ValueError('invalid login')

    monkeypatch.setattr(profile.supabase_auth, 'sign_in', bad_sign_in)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(profile.delete_account(profile.DeleteAccountIn(password='wrong', confirm='delete'), user=user))
    assert exc.value.status_code == 401
