"""Test cursor compatibility wrapper for dictionary=True."""
from __future__ import annotations

from typing import Any

from .connection import MySQLConnection


class FakeCursor:
    pass


class FakeConn:
    def __init__(self):
        self._created = False

    def cursor(self, *args, **kwargs) -> Any:
        # Return a sentinel object so tests can assert behavior
        self._created = True
        return FakeCursor()


def test_cursor_dictionary_flag_uses_underlying_cursor():
    conn = MySQLConnection()
    # Inject a fake connection object to avoid real DB calls
    conn._connection = FakeConn()

    with conn.get_connection() as c:
        cur = c.cursor(dictionary=True)
        assert isinstance(cur, FakeCursor)


def test_cursor_without_dictionary_works():
    conn = MySQLConnection()
    conn._connection = FakeConn()

    with conn.get_connection() as c:
        cur = c.cursor()
        assert isinstance(cur, FakeCursor)
