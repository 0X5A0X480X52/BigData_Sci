"""Quick smoke test for shared MySQL connector (does not require a running DB)."""
from __future__ import annotations

from .config import load_mysql_config
from .connection import MySQLConnection


def test_import_and_config():
    cfg = load_mysql_config()  # load from env or defaults
    conn = MySQLConnection(**cfg.to_dict())
    # ensure attributes are available
    assert hasattr(conn, "host")
    assert hasattr(conn, "get_connection")


if __name__ == "__main__":
    test_import_and_config()
    print("Shared MySQL connector smoke test passed")
