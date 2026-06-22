# -*- coding: utf-8 -*-
"""
文件缓存管理器

将数据以 JSON 文件形式存储在本地文件系统。
缓存目录结构: {cache_dir}/{entity_type}/{id}.json
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from .base import CacheManager

logger = logging.getLogger(__name__)


class FileCacheManager(CacheManager):
    """
    基于本地文件系统的缓存管理器
    
    特点：
    - 每个缓存项存储为独立的 JSON 文件
    - 支持按实体类型分目录存储
    - 键格式: "{entity_type}/{id}" -> 存储为 {cache_dir}/{entity_type}/{id}.json
    """
    
    def __init__(
        self,
        cache_dir: str = "./cache_data",
        overwrite: bool = False,
        encoding: str = "utf-8",
        indent: int = 2,
    ):
        """
        初始化文件缓存管理器
        
        Args:
            cache_dir: 缓存根目录
            overwrite: 是否覆盖已存在的缓存
            encoding: 文件编码
            indent: JSON 缩进（设为 None 可减小文件体积）
        """
        self.cache_dir = Path(cache_dir)
        self.overwrite = overwrite
        self.encoding = encoding
        self.indent = indent
        
        # 确保缓存目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"文件缓存初始化完成，缓存目录: {self.cache_dir.absolute()}")
    
    def _key_to_path(self, key: str) -> Path:
        """
        将缓存键转换为文件路径
        
        Args:
            key: 缓存键，格式 "{entity_type}/{id}" 或 "{id}"
            
        Returns:
            Path: 文件路径
        """
        # 确保键以 .json 结尾
        if not key.endswith(".json"):
            key = f"{key}.json"
        return self.cache_dir / key
    
    def _path_to_key(self, path: Path) -> str:
        """
        将文件路径转换为缓存键
        
        Args:
            path: 文件路径
            
        Returns:
            str: 缓存键
        """
        rel_path = path.relative_to(self.cache_dir)
        # 移除 .json 后缀
        key = str(rel_path).replace("\\", "/")
        if key.endswith(".json"):
            key = key[:-5]
        return key
    
    def save(self, key: str, data: Any) -> bool:
        """
        保存数据到 JSON 文件
        
        Args:
            key: 缓存键
            data: 要缓存的数据
            
        Returns:
            bool: 保存是否成功
        """
        file_path = self._key_to_path(key)
        
        # 检查是否已存在
        if file_path.exists() and not self.overwrite:
            logger.debug(f"缓存已存在，跳过: {key}")
            return True
        
        try:
            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 写入 JSON 文件
            with open(file_path, "w", encoding=self.encoding) as f:
                json.dump(data, f, ensure_ascii=False, indent=self.indent)
            
            logger.debug(f"缓存保存成功: {key}")
            return True
            
        except Exception as e:
            logger.error(f"缓存保存失败 [{key}]: {e}")
            return False
    
    def load(self, key: str) -> Optional[Any]:
        """
        从 JSON 文件加载数据
        
        Args:
            key: 缓存键
            
        Returns:
            缓存的数据，如果不存在返回 None
        """
        file_path = self._key_to_path(key)
        
        if not file_path.exists():
            logger.debug(f"缓存不存在: {key}")
            return None
        
        try:
            with open(file_path, "r", encoding=self.encoding) as f:
                data = json.load(f)
            logger.debug(f"缓存加载成功: {key}")
            return data
            
        except Exception as e:
            logger.error(f"缓存加载失败 [{key}]: {e}")
            return None
    
    def exists(self, key: str) -> bool:
        """
        检查缓存文件是否存在
        
        Args:
            key: 缓存键
            
        Returns:
            bool: 是否存在
        """
        return self._key_to_path(key).exists()
    
    def delete(self, key: str) -> bool:
        """
        删除缓存文件
        
        Args:
            key: 缓存键
            
        Returns:
            bool: 删除是否成功
        """
        file_path = self._key_to_path(key)
        
        if not file_path.exists():
            return True
        
        try:
            file_path.unlink()
            logger.debug(f"缓存删除成功: {key}")
            return True
        except Exception as e:
            logger.error(f"缓存删除失败 [{key}]: {e}")
            return False
    
    def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        """
        列出所有缓存键
        
        Args:
            prefix: 可选的键前缀过滤，如 "works" 只列出 works 目录下的缓存
            
        Returns:
            List[str]: 缓存键列表
        """
        keys = []
        
        # 确定搜索目录
        if prefix:
            search_dir = self.cache_dir / prefix
            if not search_dir.exists():
                return []
        else:
            search_dir = self.cache_dir
        
        # 递归查找所有 .json 文件
        for json_file in search_dir.rglob("*.json"):
            key = self._path_to_key(json_file)
            keys.append(key)
        
        return sorted(keys)
    
    def get_cache_stats(self) -> Dict[str, int]:
        """
        获取缓存统计信息
        
        Returns:
            Dict[str, int]: 各实体类型的缓存数量
        """
        stats = {}
        
        for item in self.cache_dir.iterdir():
            if item.is_dir():
                count = len(list(item.rglob("*.json")))
                stats[item.name] = count
        
        return stats
