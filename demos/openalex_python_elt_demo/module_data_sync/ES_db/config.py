"""
Elasticsearch连接配置模块
支持从环境变量或配置文件读取连接参数
"""

import os
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class ElasticsearchConfig:
    """Elasticsearch连接配置类"""
    hosts: List[str] = None  # ['localhost:9200'] or ['http://localhost:9200']
    username: Optional[str] = None
    password: Optional[str] = None
    use_ssl: bool = False
    verify_certs: bool = False
    ca_certs: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3
    retry_on_timeout: bool = True
    
    def __post_init__(self):
        if self.hosts is None:
            self.hosts = ['localhost:9200']
    
    @classmethod
    def from_env(cls) -> 'ElasticsearchConfig':
        """从环境变量加载配置"""
        hosts_str = os.getenv('ES_HOSTS', 'localhost:9200')
        hosts = [h.strip() for h in hosts_str.split(',')]
        
        return cls(
            hosts=hosts,
            username=os.getenv('ES_USERNAME'),
            password=os.getenv('ES_PASSWORD'),
            use_ssl=os.getenv('ES_USE_SSL', 'false').lower() == 'true',
            verify_certs=os.getenv('ES_VERIFY_CERTS', 'false').lower() == 'true'
        )
    
    @classmethod
    def from_dict(cls, config_dict: dict) -> 'ElasticsearchConfig':
        """从字典加载配置"""
        # normalize hosts: ensure scheme (http://) present for host strings
        data = {k: v for k, v in config_dict.items() if k in cls.__annotations__}
        hosts = data.get('hosts')
        if hosts:
            normalized = []
            for h in hosts:
                # Normalize to URL string or host dict compatible with various ES client versions
                if isinstance(h, str):
                    # ensure scheme
                    if not h.startswith(('http://', 'https://')):
                        h = f"http://{h}"

                    # parse and convert to dict
                    try:
                        from urllib.parse import urlparse
                        p = urlparse(h)
                        host_entry = {"host": p.hostname, "port": p.port or 9200, "scheme": p.scheme}
                    except Exception:
                        host_entry = h

                    normalized.append(host_entry)
                else:
                    normalized.append(h)

            data['hosts'] = normalized

        return cls(**data)


def get_config() -> ElasticsearchConfig:
    """获取默认配置（优先读取环境变量）"""
    return ElasticsearchConfig.from_env()
