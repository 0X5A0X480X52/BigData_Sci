# -*- coding: utf-8 -*-
"""
MySQL 数据库连接管理

提供数据库连接池和事务管理功能。
"""

import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class MySQLConnection:
    """
    MySQL 连接管理器
    
    提供：
    - 连接创建和管理
    - 事务上下文管理
    - 外键检查控制
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "Scientific_Info_db",
        charset: str = "utf8mb4",
    ):
        """
        初始化数据库连接参数
        
        Args:
            host: 数据库主机
            port: 端口号
            user: 用户名
            password: 密码
            database: 数据库名
            charset: 字符集
        """
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
            self.logger.info(f"数据库连接成功: {self.config['host']}:{self.config['port']}/{self.config['database']}")
            return self._connection
            
        except ImportError:
            self.logger.error("pymysql 库未安装，请运行: pip install pymysql")
            raise
        except Exception as e:
            self.logger.error(f"数据库连接失败: {e}")
            raise
    
    def close(self):
        """关闭数据库连接"""
        if self._connection:
            self._connection.close()
            self._connection = None
            self.logger.info("数据库连接已关闭")
    
    @property
    def connection(self):
        """获取当前连接，如不存在则创建"""
        if self._connection is None or not self._connection.open:
            self.connect()
        return self._connection
    
    def cursor(self, cursor_class=None):
        """获取游标"""
        if cursor_class:
            return self.connection.cursor(cursor_class)
        return self.connection.cursor()
    
    def commit(self):
        """提交事务"""
        self.connection.commit()
    
    def rollback(self):
        """回滚事务"""
        self.connection.rollback()
    
    @contextmanager
    def transaction(self):
        """
        事务上下文管理器
        
        使用方式:
            with db.transaction():
                cursor = db.cursor()
                cursor.execute(...)
        """
        try:
            yield self.connection
            self.commit()
        except Exception as e:
            self.rollback()
            self.logger.error(f"事务回滚: {e}")
            raise
    
    @contextmanager
    def disable_foreign_key_checks(self):
        """
        临时禁用外键检查的上下文管理器
        
        使用方式:
            with db.disable_foreign_key_checks():
                # 执行需要禁用外键检查的操作
        """
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
        """
        执行 SQL 语句
        
        Args:
            sql: SQL 语句
            params: 参数元组
            
        Returns:
            受影响的行数
        """
        cursor = self.cursor()
        try:
            result = cursor.execute(sql, params)
            return result
        finally:
            cursor.close()
    
    def executemany(self, sql: str, params_list: list):
        """
        批量执行 SQL 语句
        
        Args:
            sql: SQL 语句
            params_list: 参数元组列表
            
        Returns:
            受影响的行数
        """
        cursor = self.cursor()
        try:
            result = cursor.executemany(sql, params_list)
            return result
        finally:
            cursor.close()
    
    def fetchone(self, sql: str, params: tuple = None):
        """执行查询并返回单条结果"""
        cursor = self.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchone()
        finally:
            cursor.close()
    
    def fetchall(self, sql: str, params: tuple = None):
        """执行查询并返回所有结果"""
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
