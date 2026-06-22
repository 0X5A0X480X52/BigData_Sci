"""Shared MySQL connection manager (lightweight wrapper around pymysql)."""
import logging
from contextlib import contextmanager
from typing import Optional

from .config import MySQLConfig

logger = logging.getLogger(__name__)


class MySQLConnection:
    """MySQL 连接管理器（与原实现兼容）。"""

    def __init__(self, config: Optional[MySQLConfig] = None, **kwargs):
        if isinstance(config, MySQLConfig):
            self.config = config.to_dict()
        else:
            # kwargs or dict-like
            cfg = config if isinstance(config, dict) else kwargs
            self.config = MySQLConfig.from_dict(cfg).to_dict()

        self._connection = None
        self.logger = logging.getLogger(self.__class__.__name__)

    # 兼容属性（便于旧代码访问）
    @property
    def host(self) -> str:
        return self.config.get("host")

    @property
    def port(self) -> int:
        return int(self.config.get("port"))

    @property
    def database(self) -> str:
        return self.config.get("database")

    def connect(self):
        try:
            import pymysql

            self._connection = pymysql.connect(**self.config)
            self.logger.info(f"数据库连接成功: {self.config['host']}:{self.config['port']}/{self.config['database']}")
            return self._connection
        except ImportError:
            self.logger.error("pymysql 库未安装，请运行: pip install pymysql")
            raise
        except Exception as e:
            self.logger.error(f"数据库连接失败: {e}")
            raise

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None
            self.logger.info("数据库连接已关闭")

    @property
    def connection(self):
        if self._connection is None or not getattr(self._connection, "open", True):
            self.connect()
        return self._connection

    def cursor(self, cursor_class=None):
        if cursor_class:
            return self.connection.cursor(cursor_class)
        return self.connection.cursor()

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    @contextmanager
    def transaction(self):
        try:
            yield self.connection
            self.commit()
        except Exception as e:
            self.rollback()
            self.logger.error(f"事务回滚: {e}")
            raise

    @contextmanager
    def disable_foreign_key_checks(self):
        cursor = self.cursor()
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            yield
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        except Exception:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, params: tuple = None):
        cursor = self.cursor()
        try:
            result = cursor.execute(sql, params)
            return result
        finally:
            cursor.close()

    def executemany(self, sql: str, params_list: list):
        cursor = self.cursor()
        try:
            result = cursor.executemany(sql, params_list)
            return result
        finally:
            cursor.close()

    def fetchone(self, sql: str, params: tuple = None):
        cursor = self.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetchall(self, sql: str, params: tuple = None):
        cursor = self.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            cursor.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # 兼容方法
    def get_connection(self):
        """返回一个上下文管理器，yield 原生 pymysql 连接对象（兼容旧代码）。"""
        @contextmanager
        def _cm():
            conn = self.connect()
            try:
                yield conn
            finally:
                self.close()

        return _cm()
"""MySQL 数据库连接管理（共享版）

移动到 python_backend.common.DBConnector.MySQL_db 下以便多个模块复用。
"""

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class MySQLConnection:
    """MySQL 连接管理器（简洁复制自原实现）"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "Scientific_Info_db",
        charset: str = "utf8mb4",
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset

        self.config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": charset,
        }
        self._connection = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def connect(self):
        """创建数据库连接"""
        try:
            import pymysql

            self._connection = pymysql.connect(**self.config)
            self.logger.info(
                f"数据库连接成功: {self.config['host']}:{self.config['port']}/{self.config['database']}"
            )
            return self._connection

        except ImportError:
            self.logger.error("pymysql 库未安装，请运行: pip install pymysql")
            raise
        except Exception as e:
            self.logger.error(f"数据库连接失败: {e}")
            raise

    def close(self):
        if self._connection:
            try:
                self._connection.close()
            finally:
                self._connection = None
                self.logger.info("数据库连接已关闭")

    @property
    def connection(self):
        if self._connection is None or not getattr(self._connection, "open", True):
            self.connect()
        return self._connection

    def cursor(self, cursor_class=None):
        if cursor_class:
            return self.connection.cursor(cursor_class)
        return self.connection.cursor()

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    @contextmanager
    def transaction(self):
        try:
            yield self.connection
            self.commit()
        except Exception as e:
            self.rollback()
            self.logger.error(f"事务回滚: {e}")
            raise

    @contextmanager
    def disable_foreign_key_checks(self):
        cursor = self.cursor()
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            yield
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        except Exception:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, params: tuple = None):
        cursor = self.cursor()
        try:
            result = cursor.execute(sql, params)
            return result
        finally:
            cursor.close()

    def executemany(self, sql: str, params_list: list):
        cursor = self.cursor()
        try:
            result = cursor.executemany(sql, params_list)
            return result
        finally:
            cursor.close()

    def fetchone(self, sql: str, params: tuple = None):
        cursor = self.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetchall(self, sql: str, params: tuple = None):
        cursor = self.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            cursor.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # 兼容方法：返回底层连接的上下文管理器（旧代码可能使用 get_connection()）
    def get_connection(self):
        @contextmanager
        def _cm():
            conn = self.connection

            # 为兼容老代码中使用 conn.cursor(dictionary=True) 的写法，
            # 我们在返回的连接对象上临时注入一个包装 cursor 方法，
            # 当调用时如果传入了 dictionary=True，则使用 pymysql 的
            # DictCursor 来返回字典结果。
            try:
                import pymysql

                original_cursor = conn.cursor

                def _cursor_compat(*args, **kwargs):
                    # 支持 cursor(dictionary=True)
                    if 'dictionary' in kwargs:
                        dict_flag = kwargs.pop('dictionary')
                        if dict_flag:
                            return original_cursor(pymysql.cursors.DictCursor)
                        else:
                            return original_cursor()

                    # 支持 cursor(cursorclass=...) 或直接传入 cursor class
                    if 'cursorclass' in kwargs:
                        cursorclass = kwargs.pop('cursorclass')
                        return original_cursor(cursorclass)

                    if args and (isinstance(args[0], type) or hasattr(args[0], '__mro__')):
                        return original_cursor(args[0])

                    # 默认行为
                    return original_cursor(*args, **kwargs)

                # 注入兼容方法
                setattr(conn, 'cursor', _cursor_compat)
            except Exception:
                # 若任何兼容逻辑失败，不阻止正常返回原生连接
                pass

            try:
                yield conn
            finally:
                # 不自动关闭连接，保持与旧行为兼容
                pass

        return _cm()
