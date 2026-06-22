# -*- coding: utf-8 -*-
"""
OpenAlex 数据源

基于 pyalex 库实现的 OpenAlex API 数据获取。
"""

from typing import Any, Dict, Generator, List, Optional
import logging
import time

from .base import BaseSource
from ..cache import CacheManager

logger = logging.getLogger(__name__)


class OpenAlexSource(BaseSource):
    """
    OpenAlex 数据源
    
    基于 pyalex 库封装，提供：
    - Works, Authors, Institutions, Sources 的获取
    - 分页支持（使用 cursor 分页）
    - 缓存集成
    """
    
    def __init__(
        self,
        email: str = "your_email@example.com",
        cache_manager: Optional[CacheManager] = None,
        use_cache: bool = True,
        request_delay: float = 0.1,  # 请求间隔（秒）
    ):
        """
        初始化 OpenAlex 数据源
        
        Args:
            email: 用于 OpenAlex polite pool 的邮箱
            cache_manager: 缓存管理器实例
            use_cache: 是否使用缓存
            request_delay: API 请求间隔（避免超过速率限制）
        """
        super().__init__()
        
        self.email = email
        self.cache_manager = cache_manager
        self.use_cache = use_cache and cache_manager is not None
        self.request_delay = request_delay
        
        # 配置 pyalex
        self._configure_pyalex()
    
    def _configure_pyalex(self):
        """配置 pyalex 库"""
        try:
            import pyalex
            pyalex.config.email = self.email
            pyalex.config.max_retries = 3
            self.logger.info(f"pyalex 配置完成，email: {self.email}")
        except ImportError:
            self.logger.error("pyalex 库未安装，请运行: pip install pyalex")
            raise
    
    @property
    def source_name(self) -> str:
        return "OpenAlex"
    
    def _get_cache_key(self, entity_type: str, entity_id: str) -> str:
        """生成缓存键"""
        # 提取纯 ID（去除 URL 前缀）
        if "/" in entity_id:
            entity_id = entity_id.split("/")[-1]
        return f"{entity_type}/{entity_id}"
    
    def _save_to_cache(self, entity_type: str, entity_id: str, data: Dict[str, Any]):
        """保存到缓存"""
        if self.use_cache and self.cache_manager:
            key = self._get_cache_key(entity_type, entity_id)
            self.cache_manager.save(key, data)
    
    def _load_from_cache(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """从缓存加载"""
        if self.use_cache and self.cache_manager:
            key = self._get_cache_key(entity_type, entity_id)
            return self.cache_manager.load(key)
        return None
    
    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个论文
        
        Args:
            work_id: OpenAlex Work ID (如 "W2741809807" 或完整 URL)
            
        Returns:
            Work 原始 JSON 数据
        """
        # 尝试从缓存加载
        cached = self._load_from_cache("works", work_id)
        if cached:
            self.logger.debug(f"从缓存加载 Work: {work_id}")
            return cached
        
        try:
            from pyalex import Works
            
            work = Works()[work_id]
            
            # 转换为普通字典
            work_data = dict(work)
            
            # 保存到缓存
            openalex_id = work_data.get("id", work_id)
            self._save_to_cache("works", openalex_id, work_data)
            
            self.logger.debug(f"获取 Work 成功: {work_id}")
            return work_data
            
        except Exception as e:
            self.logger.error(f"获取 Work 失败 [{work_id}]: {e}")
            return None
    
    def search_works(
        self,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        per_page: int = 25,
        max_results: Optional[int] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        搜索论文（生成器）
        
        Args:
            query: 搜索关键词
            filters: 过滤条件，如 {"publication_year": 2020, "is_oa": True}
            per_page: 每页数量 (1-200)
            max_results: 最大结果数，None 表示获取所有
            
        Yields:
            Work 原始 JSON 数据
        """
        try:
            from pyalex import Works
            
            # 构建查询
            works_query = Works()
            
            # 添加搜索词
            if query:
                works_query = works_query.search(query)
            
            # 添加过滤条件
            if filters:
                works_query = works_query.filter(**filters)
            
            # 使用 cursor 分页
            per_page = min(max(1, per_page), 200)  # 限制范围
            
            count = 0
            for page in works_query.paginate(per_page=per_page, n_max=max_results):
                for work in page:
                    # 转换为普通字典
                    work_data = dict(work)
                    
                    # 保存到缓存
                    openalex_id = work_data.get("id")
                    if openalex_id:
                        self._save_to_cache("works", openalex_id, work_data)
                    
                    yield work_data
                    
                    count += 1
                    if max_results and count >= max_results:
                        return
                
                # 请求间隔
                if self.request_delay > 0:
                    time.sleep(self.request_delay)
            
            self.logger.info(f"搜索完成，共获取 {count} 条结果")
            
        except Exception as e:
            self.logger.error(f"搜索 Works 失败: {e}")
            raise
    
    def get_works_by_concept(
        self,
        concept_id: str,
        per_page: int = 50,
        max_results: Optional[int] = 1000,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        按概念获取论文
        
        Args:
            concept_id: OpenAlex Concept ID
            per_page: 每页数量
            max_results: 最大结果数
            
        Yields:
            Work 原始 JSON 数据
        """
        # 提取纯 ID
        if "/" in concept_id:
            concept_id = concept_id.split("/")[-1]
        
        filters = {"concept": {"id": concept_id}}
        yield from self.search_works(filters=filters, per_page=per_page, max_results=max_results)
    
    def get_works_by_institution(
        self,
        institution_id: str,
        per_page: int = 50,
        max_results: Optional[int] = 1000,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        按机构获取论文
        
        Args:
            institution_id: OpenAlex Institution ID 或 ROR ID
            per_page: 每页数量
            max_results: 最大结果数
            
        Yields:
            Work 原始 JSON 数据
        """
        filters = {"authorships": {"institutions": {"id": institution_id}}}
        yield from self.search_works(filters=filters, per_page=per_page, max_results=max_results)
    
    def get_author(self, author_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个作者
        
        Args:
            author_id: OpenAlex Author ID
            
        Returns:
            Author 原始 JSON 数据
        """
        # 尝试从缓存加载
        cached = self._load_from_cache("authors", author_id)
        if cached:
            self.logger.debug(f"从缓存加载 Author: {author_id}")
            return cached
        
        try:
            from pyalex import Authors
            
            author = Authors()[author_id]
            author_data = dict(author)
            
            openalex_id = author_data.get("id", author_id)
            self._save_to_cache("authors", openalex_id, author_data)
            
            return author_data
            
        except Exception as e:
            self.logger.error(f"获取 Author 失败 [{author_id}]: {e}")
            return None
    
    def get_institution(self, institution_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个机构
        
        Args:
            institution_id: OpenAlex Institution ID
            
        Returns:
            Institution 原始 JSON 数据
        """
        # 尝试从缓存加载
        cached = self._load_from_cache("institutions", institution_id)
        if cached:
            self.logger.debug(f"从缓存加载 Institution: {institution_id}")
            return cached
        
        try:
            from pyalex import Institutions
            
            inst = Institutions()[institution_id]
            inst_data = dict(inst)
            
            openalex_id = inst_data.get("id", institution_id)
            self._save_to_cache("institutions", openalex_id, inst_data)
            
            return inst_data
            
        except Exception as e:
            self.logger.error(f"获取 Institution 失败 [{institution_id}]: {e}")
            return None
    
    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个期刊/会议来源
        
        Args:
            source_id: OpenAlex Source ID
            
        Returns:
            Source 原始 JSON 数据
        """
        # 尝试从缓存加载
        cached = self._load_from_cache("sources", source_id)
        if cached:
            self.logger.debug(f"从缓存加载 Source: {source_id}")
            return cached
        
        try:
            from pyalex import Sources
            
            source = Sources()[source_id]
            source_data = dict(source)
            
            openalex_id = source_data.get("id", source_id)
            self._save_to_cache("sources", openalex_id, source_data)
            
            return source_data
            
        except Exception as e:
            self.logger.error(f"获取 Source 失败 [{source_id}]: {e}")
            return None
    
    def search_concepts(
        self,
        keyword: str,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        搜索概念
        
        Args:
            keyword: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            Concept 列表
        """
        try:
            from pyalex import Concepts
            
            results = Concepts().search(keyword).get(per_page=max_results)
            return [dict(c) for c in results]
            
        except Exception as e:
            self.logger.error(f"搜索 Concepts 失败: {e}")
            return []
    
    def get_works_by_ids(
        self,
        work_ids: List[str],
        batch_size: int = 50,
        show_progress: bool = False,
        progress_desc: str = "获取被引论文",
    ) -> Generator[Dict[str, Any], None, None]:
        """
        批量获取指定 ID 的论文
        
        使用 OpenAlex API 的 filter 功能批量获取论文，比逐个获取更高效。
        
        Args:
            work_ids: OpenAlex Work ID 列表（可以是完整 URL 或短 ID）
            batch_size: 每批请求的 ID 数量（API 限制约 50 个）
            show_progress: 是否显示进度条
            progress_desc: 进度条描述
            
        Yields:
            Work 原始 JSON 数据
        """
        if not work_ids:
            return
        
        # 标准化 ID（提取短 ID）
        normalized_ids = []
        for wid in work_ids:
            if "/" in wid:
                wid = wid.split("/")[-1]
            normalized_ids.append(wid)
        
        total_ids = len(normalized_ids)
        total_batches = (total_ids + batch_size - 1) // batch_size
        
        # 初始化进度条
        pbar = None
        if show_progress:
            try:
                from tqdm import tqdm
                pbar = tqdm(total=total_ids, desc=progress_desc, unit="篇")
            except ImportError:
                self.logger.warning("tqdm 未安装，跳过进度条显示")
        
        fetched_count = 0
        
        try:
            # 分批处理
            for i in range(0, len(normalized_ids), batch_size):
                batch_ids = normalized_ids[i:i + batch_size]
                
                # 先检查缓存
                uncached_ids = []
                for wid in batch_ids:
                    cached = self._load_from_cache("works", wid)
                    if cached:
                        self.logger.debug(f"从缓存加载 Work: {wid}")
                        yield cached
                        fetched_count += 1
                        if pbar:
                            pbar.update(1)
                    else:
                        uncached_ids.append(wid)
                
                # 批量获取未缓存的
                if uncached_ids:
                    try:
                        from pyalex import Works
                        
                        # 使用 OR 过滤批量获取
                        id_filter = "|".join(uncached_ids)
                        works_query = Works().filter(openalex_id=id_filter)
                        
                        for work in works_query.get():
                            work_data = dict(work)
                            openalex_id = work_data.get("id")
                            
                            if openalex_id:
                                self._save_to_cache("works", openalex_id, work_data)
                            
                            yield work_data
                            fetched_count += 1
                            if pbar:
                                pbar.update(1)
                        
                        # 请求间隔
                        if self.request_delay > 0:
                            time.sleep(self.request_delay)
                        
                    except Exception as e:
                        self.logger.error(f"批量获取 Works 失败: {e}")
                        # 降级为逐个获取
                        for wid in uncached_ids:
                            work_data = self.get_work(wid)
                            if work_data:
                                yield work_data
                                fetched_count += 1
                                if pbar:
                                    pbar.update(1)
        finally:
            if pbar:
                pbar.close()
    
    def count_works(
        self,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        统计论文数量
        
        Args:
            query: 搜索关键词
            filters: 过滤条件
            
        Returns:
            论文数量
        """
        try:
            from pyalex import Works
            
            works_query = Works()
            
            if query:
                works_query = works_query.search(query)
            
            if filters:
                works_query = works_query.filter(**filters)
            
            return works_query.count()
            
        except Exception as e:
            self.logger.error(f"统计 Works 失败: {e}")
            return 0
