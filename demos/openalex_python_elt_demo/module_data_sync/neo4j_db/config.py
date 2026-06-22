"""
Neo4j数据库连接配置模块
支持从环境变量或配置文件读取连接参数
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Neo4jConfig:
    """Neo4j连接配置类"""
    uri: str = "bolt://localhost:7687"
    username: str = "neo4j"
    password: str = "password"
    database: str = "neo4j"  # Neo4j 4.0+ 支持多数据库
    max_connection_lifetime: int = 3600
    max_connection_pool_size: int = 50
    connection_acquisition_timeout: int = 60
    
    @classmethod
    def from_env(cls) -> 'Neo4jConfig':
        """从环境变量加载配置"""
        return cls(
            uri=os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
            username=os.getenv('NEO4J_USERNAME', 'neo4j'),
            password=os.getenv('NEO4J_PASSWORD', 'password'),
            database=os.getenv('NEO4J_DATABASE', 'neo4j')
        )
    
    @classmethod
    def from_dict(cls, config_dict: dict) -> 'Neo4jConfig':
        """从字典加载配置"""
        return cls(**{k: v for k, v in config_dict.items() if k in cls.__annotations__})


def get_config() -> Neo4jConfig:
    """获取默认配置（优先读取环境变量）"""
    return Neo4jConfig.from_env()
