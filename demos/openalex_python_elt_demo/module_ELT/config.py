# -*- coding: utf-8 -*-
"""
配置文件

集中管理所有配置项：
- pyalex API 配置
- MySQL 数据库连接配置
- 缓存配置
- 日志配置
"""

import os
from dataclasses import dataclass, field
from typing import Literal, Optional

try:
    # 优先使用共享的 MySQLConfig
    from python_backend.common.DBConnector.MySQL_db.config import MySQLConfig  # type: ignore
except Exception:
    @dataclass
    class MySQLConfig:
        """MySQL 数据库连接配置"""
        host: str = "localhost"
        port: int = 3306
        user: str = "root"
        password: str = ""
        database: str = "Scientific_Info_db"
        charset: str = "utf8mb4"

        def to_dict(self) -> dict:
            """转换为 pymysql 连接参数"""
            return {
                "host": self.host,
                "port": self.port,
                "user": self.user,
                "password": self.password,
                "database": self.database,
                "charset": self.charset,
            }


@dataclass
class PyAlexConfig:
    """pyalex API 配置"""
    email: str = "your_email@example.com"  # OpenAlex polite pool 必需
    max_retries: int = 3
    retry_delay: float = 1.0  # 重试间隔（秒）


@dataclass
class MySQLConfig:
    """MySQL 数据库连接配置"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "Scientific_Info_db"
    charset: str = "utf8mb4"
    
    def to_dict(self) -> dict:
        """转换为 pymysql 连接参数"""
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": self.charset,
        }


@dataclass
class CacheConfig:
    """缓存配置"""
    # 缓存后端: "file" 或 "mongo"
    backend: Literal["file", "mongo"] = "file"
    
    # 文件缓存配置
    file_cache_dir: str = "./cache_data"
    
    # MongoDB 缓存配置（预留）
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "openalex_cache"
    
    # 缓存策略
    enable_cache: bool = True
    overwrite_existing: bool = False  # 是否覆盖已存在的缓存


@dataclass
class LogConfig:
    """日志配置"""
    level: str = "INFO"
    log_file: Optional[str] = "./logs/etl.log"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


@dataclass
class ETLConfig:
    """ETL 总配置"""
    pyalex: PyAlexConfig = field(default_factory=PyAlexConfig)
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    log: LogConfig = field(default_factory=LogConfig)
    
    # 批处理配置
    batch_size: int = 100  # 每批处理的记录数
    
    @classmethod
    def from_env(cls) -> "ETLConfig":
        """从环境变量加载配置"""
        config = cls()
        
        # PyAlex 配置
        if os.getenv("PYALEX_EMAIL"):
            config.pyalex.email = os.getenv("PYALEX_EMAIL")
        
        # MySQL 配置
        if os.getenv("MYSQL_HOST"):
            config.mysql.host = os.getenv("MYSQL_HOST")
        if os.getenv("MYSQL_PORT"):
            config.mysql.port = int(os.getenv("MYSQL_PORT"))
        if os.getenv("MYSQL_USER"):
            config.mysql.user = os.getenv("MYSQL_USER")
        if os.getenv("MYSQL_PASSWORD"):
            config.mysql.password = os.getenv("MYSQL_PASSWORD")
        if os.getenv("MYSQL_DATABASE"):
            config.mysql.database = os.getenv("MYSQL_DATABASE")
        
        # 缓存配置
        if os.getenv("CACHE_BACKEND"):
            config.cache.backend = os.getenv("CACHE_BACKEND")
        if os.getenv("CACHE_DIR"):
            config.cache.file_cache_dir = os.getenv("CACHE_DIR")
        
        return config


# 全局默认配置实例
default_config = ETLConfig()
