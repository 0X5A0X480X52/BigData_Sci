"""Simple unit tests for the shared MySQL connector (no network calls)."""
from .config import MySQLConfig
from .connection import MySQLConnection


def test_config_to_dict_and_back():
    cfg = MySQLConfig(host="127.0.0.1", port=3307, user="u", password="p", database="test_db")
    d = cfg.to_dict()
    assert d["host"] == "127.0.0.1"
    cfg2 = MySQLConfig.from_dict(d)
    assert cfg2.host == "127.0.0.1"


def test_connection_init_compat():
    cfg = {"host": "127.0.0.1", "port": 3307, "user": "u", "password": "p", "database": "test_db"}
    conn = MySQLConnection(**cfg)
    assert conn.host == "127.0.0.1"
    assert conn.port == 3307
    assert conn.database == "test_db"


if __name__ == "__main__":
    test_config_to_dict_and_back()
    test_connection_init_compat()
    print("All tests passed (unit).")
