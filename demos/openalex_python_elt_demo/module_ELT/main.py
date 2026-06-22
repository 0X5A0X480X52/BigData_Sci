# -*- coding: utf-8 -*-
"""
ETL 主入口

串联数据获取 -> 缓存 -> 清洗 -> 入库的完整流程。
"""

import logging
import sys
import os
from typing import Optional, Dict, Any, List

# Support running as a module (package) and as a script
try:
    # When run as a package: python -m <package>.main
    from .config import ETLConfig, default_config
    from .sources import OpenAlexSource
    from .pipelines import OpenAlexPipeline
    from .cache import get_cache_manager, CacheManager
    from .db import MySQLConnection, MySQLInserter
except (ImportError, SystemError):
    # When run as a script: python main.py
    # Import package and submodules via package name (preserve relative imports)
    pkg_root = os.path.dirname(os.path.abspath(__file__))
    pkg_parent = os.path.dirname(pkg_root)
    pkg_name = os.path.basename(pkg_root)

    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)

    import importlib
    pkg = importlib.import_module(pkg_name)
    cfg_mod = importlib.import_module(f"{pkg_name}.config")
    src_mod = importlib.import_module(f"{pkg_name}.sources")
    pipelines_mod = importlib.import_module(f"{pkg_name}.pipelines")
    cache_mod = importlib.import_module(f"{pkg_name}.cache")
    db_mod = importlib.import_module(f"{pkg_name}.db")

    ETLConfig = cfg_mod.ETLConfig
    default_config = cfg_mod.default_config
    OpenAlexSource = src_mod.OpenAlexSource
    OpenAlexPipeline = pipelines_mod.OpenAlexPipeline
    get_cache_manager = cache_mod.get_cache_manager
    CacheManager = cache_mod.CacheManager
    MySQLConnection = db_mod.MySQLConnection
    MySQLInserter = db_mod.MySQLInserter


def setup_logging(config: Any):
    """配置日志"""
    log_format = config.log.format
    log_level = getattr(logging, config.log.level.upper(), logging.INFO)
    
    # 配置根日志器
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )
    
    # 如果配置了日志文件，添加文件处理器
    if config.log.log_file:
        import os
        log_dir = os.path.dirname(config.log.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        
        file_handler = logging.FileHandler(config.log.log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(log_format))
        logging.getLogger().addHandler(file_handler)


class OpenAlexETL:
    """
    OpenAlex ETL 流程管理器
    
    使用示例:
    ```python
    from etl import OpenAlexETL, ETLConfig
    
    config = ETLConfig()
    config.mysql.password = "your_password"
    config.pyalex.email = "your_email@example.com"
    
    etl = OpenAlexETL(config)
    
    # 按关键词搜索并入库
    stats = etl.run(
        query="machine learning",
        filters={"publication_year": 2023},
        max_results=100
    )
    print(stats)
    ```
    """
    
    def __init__(self, config: Optional[Any] = None):
        """
        初始化 ETL 管理器
        
        Args:
            config: ETL 配置，如不提供则使用默认配置
        """
        self.config = config or default_config
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 设置日志
        setup_logging(self.config)
        
        # 初始化组件
        self._cache_manager: Optional[Any] = None
        self._source: Optional[Any] = None
        self._pipeline: Optional[Any] = None
        self._db: Optional[Any] = None
        self._inserter: Optional[Any] = None
    
    @property
    def cache_manager(self) -> Any:
        """获取缓存管理器（懒加载）"""
        if self._cache_manager is None:
            self._cache_manager = get_cache_manager(
                backend=self.config.cache.backend,
                cache_dir=self.config.cache.file_cache_dir,
                overwrite=self.config.cache.overwrite_existing,
            )
        return self._cache_manager
    
    @property
    def source(self) -> Any:
        """获取数据源（懒加载）"""
        if self._source is None:
            cache = self.cache_manager if self.config.cache.enable_cache else None
            self._source = OpenAlexSource(
                email=self.config.pyalex.email,
                cache_manager=cache,
                use_cache=self.config.cache.enable_cache,
            )
        return self._source
    
    @property
    def pipeline(self) -> Any:
        """获取清洗流水线（懒加载）"""
        if self._pipeline is None:
            self._pipeline = OpenAlexPipeline()
        return self._pipeline
    
    @property
    def db(self) -> Any:
        """获取数据库连接（懒加载）"""
        if self._db is None:
            self._db = MySQLConnection(**self.config.mysql.to_dict())
        return self._db
    
    @property
    def inserter(self) -> Any:
        """获取数据插入器（懒加载）"""
        if self._inserter is None:
            self._inserter = MySQLInserter(self.db)
        return self._inserter
    
    def run(
        self,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        max_results: Optional[int] = 100,
        per_page: int = 50,
        skip_db_insert: bool = False,
        citation_depth: int = 0,
        max_citations_per_work: int = 50,
    ) -> Dict[str, Any]:
        """
        执行完整的 ETL 流程
        
        Args:
            query: 搜索关键词
            filters: 过滤条件
            max_results: 最大获取数量
            per_page: 每页数量
            skip_db_insert: 是否跳过数据库插入（仅获取和清洗）
            citation_depth: 引用递归深度（0=不递归，1=获取直接引用，2=获取引用的引用...）
            max_citations_per_work: 每篇论文最多获取的引用数量（避免爆炸式增长）
            
        Returns:
            执行统计结果
        """
        self.logger.info("=" * 50)
        self.logger.info("开始 ETL 流程")
        self.logger.info(f"查询: {query}, 过滤: {filters}, 最大数量: {max_results}")
        if citation_depth > 0:
            self.logger.info(f"引用递归深度: {citation_depth}, 每论文最大引用: {max_citations_per_work}")
        self.logger.info("=" * 50)
        
        stats = {
            "fetched": 0,
            "cleaned": 0,
            "inserted": {},
            "errors": 0,
            "citation_stats": {
                "total_citations": 0,
                "resolved_citations": 0,
                "works_from_citations": 0,
            }
        }
        
        try:
            # 1. 数据获取
            self.logger.info("[1/4] 开始获取数据...")
            raw_data_list = []
            
            for work_data in self.source.search_works(
                query=query,
                filters=filters,
                per_page=per_page,
                max_results=max_results,
            ):
                raw_data_list.append(work_data)
                stats["fetched"] += 1
                
                if stats["fetched"] % 50 == 0:
                    self.logger.info(f"  已获取 {stats['fetched']} 条记录...")
            
            self.logger.info(f"  数据获取完成，共 {stats['fetched']} 条")
            
            # 2. 数据清洗
            self.logger.info("[2/4] 开始清洗数据...")
            batch_result = self.pipeline.process_batch(raw_data_list)
            stats["cleaned"] = batch_result.success_count
            stats["errors"] = batch_result.error_count
            
            self.logger.info(f"  数据清洗完成: 成功 {stats['cleaned']}, 失败 {stats['errors']}")
            self.logger.info(f"  去重统计: {batch_result.summary()}")
            
            # 3. 递归获取引用的论文
            if citation_depth > 0:
                self.logger.info(f"[3/4] 开始递归获取引用论文（深度: {citation_depth}）...")
                citation_stats = self._fetch_citations_recursive(
                    batch_result=batch_result,
                    depth=citation_depth,
                    max_per_work=max_citations_per_work,
                )
                stats["citation_stats"] = citation_stats
                self.logger.info(f"  引用获取完成: {citation_stats}")
            else:
                self.logger.info("[3/4] 跳过引用递归获取（depth=0）")
            
            # 4. 数据入库
            if not skip_db_insert:
                self.logger.info("[4/4] 开始写入数据库...")
                stats["inserted"] = self.inserter.insert_batch(batch_result)
                self.logger.info(f"  数据库写入完成: {stats['inserted']}")
            else:
                self.logger.info("[4/4] 跳过数据库写入")
            
            self.logger.info("=" * 50)
            self.logger.info("ETL 流程完成")
            self.logger.info(f"统计: {stats}")
            self.logger.info("=" * 50)
            
        except Exception as e:
            self.logger.error(f"ETL 流程出错: {e}")
            raise
        
        finally:
            self.close()
        
        return stats
    
    def _fetch_citations_recursive(
        self,
        batch_result: Any,
        depth: int,
        max_per_work: int = 50,
    ) -> Dict[str, int]:
        """
        递归获取引用的论文
        
        策略：
        1. 收集当前批次中所有被引用但不存在的论文 ID
        2. 批量获取这些论文
        3. 清洗并添加到 batch_result
        4. 如果 depth > 1，递归处理新获取论文的引用
        
        Args:
            batch_result: 当前的批量清洗结果
            depth: 剩余递归深度
            max_per_work: 每篇论文最多处理的引用数量
            
        Returns:
            统计信息
        """
        stats = {
            "total_citations": len(batch_result.all_citations),
            "resolved_citations": 0,
            "works_from_citations": 0,
        }
        
        if depth <= 0:
            return stats
        
        # 收集已有的论文 ID
        existing_work_ids = set()
        for result in batch_result.results:
            if result.work and result.work.openalex_id:
                existing_work_ids.add(result.work.openalex_id)
        
        # 收集需要获取的被引论文 ID
        missing_work_ids = set()
        for citation in batch_result.all_citations:
            cited_id = citation.cited_work_openalex_id
            if cited_id and cited_id not in existing_work_ids:
                missing_work_ids.add(cited_id)
        
        # 限制每层获取的数量（避免爆炸式增长）
        max_fetch_per_level = max_per_work * len(existing_work_ids)
        if len(missing_work_ids) > max_fetch_per_level:
            self.logger.warning(
                f"  被引论文数量 ({len(missing_work_ids)}) 超过限制 ({max_fetch_per_level})，截断处理"
            )
            missing_work_ids = set(list(missing_work_ids)[:max_fetch_per_level])
        
        if not missing_work_ids:
            self.logger.info("  没有需要获取的被引论文")
            return stats
        
        self.logger.info(f"  发现 {len(missing_work_ids)} 篇被引论文需要获取（深度 {depth}）")
        
        # 批量获取被引论文（带进度条）
        new_raw_data = []
        for work_data in self.source.get_works_by_ids(
            list(missing_work_ids),
            show_progress=True,
            progress_desc=f"获取被引论文(深度{depth})",
        ):
            new_raw_data.append(work_data)
        
        self.logger.info(f"  成功获取 {len(new_raw_data)} 篇被引论文")
        stats["works_from_citations"] += len(new_raw_data)
        
        if not new_raw_data:
            return stats
        
        # 清洗新获取的论文
        new_batch = self.pipeline.process_batch(new_raw_data)
        
        # 合并到主结果
        for result in new_batch.results:
            batch_result.add_result(result)
        
        # 更新已有论文 ID
        for result in new_batch.results:
            if result.work and result.work.openalex_id:
                existing_work_ids.add(result.work.openalex_id)
        
        # 统计可解析的引用数量
        for citation in batch_result.all_citations:
            if citation.cited_work_openalex_id in existing_work_ids:
                stats["resolved_citations"] += 1
        
        # 递归处理下一层
        if depth > 1 and new_batch.all_citations:
            self.logger.info(f"  递归获取下一层引用（剩余深度: {depth - 1}）")
            sub_stats = self._fetch_citations_recursive(
                batch_result=batch_result,
                depth=depth - 1,
                max_per_work=max_per_work,
            )
            stats["works_from_citations"] += sub_stats["works_from_citations"]
            stats["resolved_citations"] = sub_stats["resolved_citations"]
            stats["total_citations"] = sub_stats["total_citations"]
        
        return stats
    
    def run_from_cache(
        self,
        entity_type: str = "works",
        skip_db_insert: bool = False,
    ) -> Dict[str, Any]:
        """
        从缓存读取数据并执行清洗和入库
        
        Args:
            entity_type: 实体类型（目前仅支持 "works"）
            skip_db_insert: 是否跳过数据库插入
            
        Returns:
            执行统计结果
        """
        self.logger.info("从缓存执行 ETL 流程...")
        
        stats = {
            "loaded": 0,
            "cleaned": 0,
            "inserted": {},
            "errors": 0,
        }
        
        try:
            # 1. 从缓存加载
            keys = self.cache_manager.list_keys(prefix=entity_type)
            self.logger.info(f"发现 {len(keys)} 个缓存文件")
            
            raw_data_list = []
            for key in keys:
                data = self.cache_manager.load(key)
                if data:
                    raw_data_list.append(data)
                    stats["loaded"] += 1
            
            self.logger.info(f"加载完成，共 {stats['loaded']} 条")
            
            # 2. 数据清洗
            batch_result = self.pipeline.process_batch(raw_data_list)
            stats["cleaned"] = batch_result.success_count
            stats["errors"] = batch_result.error_count
            
            # 3. 数据入库
            if not skip_db_insert:
                stats["inserted"] = self.inserter.insert_batch(batch_result)
            
            self.logger.info(f"从缓存 ETL 完成: {stats}")
            
        except Exception as e:
            self.logger.error(f"从缓存 ETL 出错: {e}")
            raise
        
        finally:
            self.close()
        
        return stats
    
    def fetch_single_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        """
        获取并处理单个论文
        
        Args:
            work_id: OpenAlex Work ID
            
        Returns:
            清洗后的结果
        """
        # 获取
        work_data = self.source.get_work(work_id)
        if not work_data:
            return None
        
        # 清洗
        result = self.pipeline.process(work_data)
        
        return result.summary() if result.is_valid() else None
    
    def close(self):
        """关闭所有连接"""
        if self._db:
            self._db.close()
            self._db = None
        
        self._inserter = None


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="OpenAlex ETL 工具")
    parser.add_argument("--query", "-q", type=str, help="搜索关键词")
    parser.add_argument("--year", "-y", type=int, help="发表年份过滤")
    parser.add_argument("--max", "-m", type=int, default=100, help="最大获取数量")
    parser.add_argument("--email", "-e", type=str, help="OpenAlex polite pool 邮箱")
    parser.add_argument("--db-host", type=str, default="localhost", help="数据库主机")
    parser.add_argument("--db-user", type=str, default="root", help="数据库用户")
    parser.add_argument("--db-pass", type=str, default="", help="数据库密码")
    parser.add_argument("--db-name", type=str, default="Scientific_Info_db", help="数据库名")
    parser.add_argument("--cache-dir", type=str, default="./cache_data", help="缓存目录")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    parser.add_argument("--no-db", action="store_true", help="跳过数据库写入")
    parser.add_argument("--from-cache", action="store_true", help="从缓存读取数据")
    parser.add_argument(
        "--citation-depth", "-d", type=int, default=0,
        help="引用递归深度（0=不递归，1=获取直接引用的论文，2=获取引用的引用...）"
    )
    parser.add_argument(
        "--max-citations", type=int, default=50,
        help="每篇论文最多获取的引用数量（防止数据量爆炸）"
    )
    
    args = parser.parse_args()
    
    # 构建配置
    config = ETLConfig()
    
    if args.email:
        config.pyalex.email = args.email
    
    config.mysql.host = args.db_host
    config.mysql.user = args.db_user
    config.mysql.password = args.db_pass
    config.mysql.database = args.db_name
    
    config.cache.file_cache_dir = args.cache_dir
    config.cache.enable_cache = not args.no_cache
    
    # 构建过滤条件
    filters = {}
    if args.year:
        filters["publication_year"] = args.year
    
    # 执行 ETL
    etl = OpenAlexETL(config)
    
    if args.from_cache:
        stats = etl.run_from_cache(skip_db_insert=args.no_db)
    else:
        if not args.query and not filters:
            parser.error("请提供搜索关键词 (--query) 或过滤条件 (--year)")
        
        stats = etl.run(
            query=args.query,
            filters=filters if filters else None,
            max_results=args.max,
            skip_db_insert=args.no_db,
            citation_depth=args.citation_depth,
            max_citations_per_work=args.max_citations,
        )
    
    print(f"\n执行结果: {stats}")


if __name__ == "__main__":
    main()
