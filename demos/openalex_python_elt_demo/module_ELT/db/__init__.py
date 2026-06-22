# -*- coding: utf-8 -*-
"""
数据库模块

提供 MySQL 连接管理和数据插入功能。
"""

"""数据库模块

优先从共享的 `python_backend.common.DBConnector.MySQL_db` 导入 `MySQLConnection`，
如果找不到则回退到本地实现（保持向后兼容）。
"""

try:
	from python_backend.common.DBConnector.MySQL_db import MySQLConnection
except Exception:
	# 回退到本模块的本地实现
	from .connection import MySQLConnection  # type: ignore

from .inserter import MySQLInserter

__all__ = ["MySQLConnection", "MySQLInserter"]
