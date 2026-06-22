"""
MySQL主从数据库同步系统 - 主入口脚本
统一管理Neo4j和Elasticsearch的同步任务
"""

import sys
import os
import argparse
import yaml
from datetime import datetime
from typing import Optional, Dict, Any

# 使用包导入，避免修改 sys.path（共享连接器位于 python_backend.common.DBConnector）

from python_backend.module_data_sync.neo4j_db.config import Neo4jConfig
from python_backend.module_data_sync.neo4j_db.sync_manager import Neo4jSyncManager
from python_backend.module_data_sync.ES_db.config import ElasticsearchConfig
from python_backend.module_data_sync.ES_db.sync_manager import ESSyncManager


def load_config(config_file: str = 'config.yaml') -> Dict[str, Any]:
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(__file__), config_file)
    
    if not os.path.exists(config_path):
        print(f"✗ 配置文件不存在: {config_path}")
        print("  请先复制 config.yaml.example 并修改配置")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return config


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='MySQL主从数据库同步系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    try:
        try:
            from python_backend.common.DBConnector.MySQL_db import MySQLConnection  # type: ignore
        except Exception:
            from db.connection import MySQLConnection  # type: ignore

        mysql_conn = MySQLConnection(
            host=config['mysql']['host'],
            port=config['mysql']['port'],
            user=config['mysql']['user'],
            password=config['mysql']['password'],
            database=config['mysql']['database']
        )
  
  # 只同步到Elasticsearch
  python sync_all.py --mode full --target elasticsearch
  
  # 使用自定义配置文件
  python sync_all.py --config custom_config.yaml
  
  # 测试所有数据库连接
  python sync_all.py --test-connections
        """
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='配置文件路径 (默认: config.yaml)'
    )
    
    parser.add_argument(
        '--mode',
        choices=['full', 'incremental'],
        help='同步模式: full=全量同步, incremental=增量同步'
    )
    
    parser.add_argument(
        '--since',
        type=str,
        help='增量同步起始时间 (格式: YYYY-MM-DD HH:MM:SS)'
    )
    
    parser.add_argument(
        '--target',
        choices=['all', 'neo4j', 'elasticsearch'],
        default='all',
        help='同步目标 (默认: all)'
    )
    
    parser.add_argument(
        '--test-connections',
        action='store_true',
        help='测试所有数据库连接'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='试运行模式（不实际写入数据）'
    )
    
    return parser.parse_args()


def parse_datetime(datetime_str: str) -> datetime:
    """解析日期时间字符串"""
    try:
        return datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        print(f"✗ 日期格式错误: {datetime_str}")
        print("  正确格式: YYYY-MM-DD HH:MM:SS")
        sys.exit(1)


def test_connections(config: Dict[str, Any]):
    """测试所有数据库连接"""
    print("\n" + "="*60)
    print("  数据库连接测试")
    print("="*60)
    
    all_success = True
    
    # 测试MySQL
    print("\n【MySQL主库】")
    try:
        # 优先使用共享的 MySQL 连接器
        try:
            from python_backend.common.DBConnector.MySQL_db import MySQLConnection  # type: ignore
        except Exception:
            from db.connection import MySQLConnection  # type: ignore

        mysql_conn = MySQLConnection(
            host=config['mysql']['host'],
            port=config['mysql']['port'],
            user=config['mysql']['user'],
            password=config['mysql']['password'],
            database=config['mysql']['database']
        )

        with mysql_conn.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
            print(f"  ✓ 连接成功: MySQL {version}")

        mysql_conn.close()
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        all_success = False
    
    # 测试Neo4j
    print("\n【Neo4j从库】")
    try:
        neo4j_config = Neo4jConfig(**config['neo4j'])
        # 使用第三方 neo4j.Driver（不要依赖局部包名）
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            neo4j_config.uri,
            auth=(neo4j_config.username, neo4j_config.password)
        )
        driver.verify_connectivity()
        print(f"  ✓ 连接成功: {neo4j_config.uri}")
        driver.close()
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        all_success = False
    
    # 测试Elasticsearch
    print("\n【Elasticsearch从库】")
    try:
        # use from_dict to allow host normalization (scheme/port)
        es_config = ElasticsearchConfig.from_dict(config['elasticsearch'])
        from elasticsearch import Elasticsearch
        
        if es_config.username and es_config.password:
            es = Elasticsearch(
                es_config.hosts,
                basic_auth=(es_config.username, es_config.password)
            )
        else:
            es = Elasticsearch(es_config.hosts)
        
        if es.ping():
            info = es.info()
            print(f"  ✓ 连接成功: {es_config.hosts[0]} (v{info['version']['number']})")
        else:
            print(f"  ✗ 连接失败: 无法ping通")
            all_success = False
        es.close()
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        all_success = False
    
    print("\n" + "="*60)
    if all_success:
        print("  ✓ 所有连接测试通过")
    else:
        print("  ✗ 部分连接测试失败，请检查配置")
    print("="*60)
    
    return all_success


def sync_to_neo4j(config: Dict[str, Any], mode: str, since: Optional[datetime]):
    """同步到Neo4j"""
    print("\n" + "="*60)
    print("  开始同步到 Neo4j")
    print("="*60)
    
    neo4j_config = Neo4jConfig.from_dict(config['neo4j'])
    mysql_config = config['mysql']
    
    with Neo4jSyncManager(neo4j_config, mysql_config) as manager:
        manager.batch_size = config['sync']['batch_size']['neo4j']
        
        incremental = (mode == 'incremental')
        manager.sync_all(incremental=incremental, last_sync_time=since)


def sync_to_elasticsearch(config: Dict[str, Any], mode: str, since: Optional[datetime]):
    """同步到Elasticsearch"""
    print("\n" + "="*60)
    print("  开始同步到 Elasticsearch")
    print("="*60)
    
    es_config = ElasticsearchConfig.from_dict(config['elasticsearch'])
    mysql_config = config['mysql']
    
    with ESSyncManager(es_config, mysql_config) as manager:
        manager.batch_size = config['sync']['batch_size']['elasticsearch']
        
        incremental = (mode == 'incremental')
        manager.sync_all(incremental=incremental, last_sync_time=since)


def main():
    """主函数"""
    args = parse_args()
    
    # 加载配置
    config = load_config(args.config)
    
    # 测试连接模式
    if args.test_connections:
        success = test_connections(config)
        sys.exit(0 if success else 1)
    
    # 确定同步模式
    mode = args.mode or config['sync'].get('mode', 'full')
    
    # 解析同步时间
    since = None
    if mode == 'incremental':
        if args.since:
            since = parse_datetime(args.since)
        elif config['sync'].get('since'):
            since = datetime.fromisoformat(config['sync']['since'])
        else:
            print("✗ 增量同步模式需要指定 --since 参数或在配置文件中设置")
            sys.exit(1)
    
    # 试运行提示
    if args.dry_run:
        print("\n⚠ 试运行模式：将只显示同步计划，不会实际写入数据")
        print("  (实际的试运行功能需要在同步管理器中实现)\n")
    
    start_time = datetime.now()
    
    try:
        # 根据目标执行同步
        if args.target in ['all', 'neo4j']:
            sync_to_neo4j(config, mode, since)
        
        if args.target in ['all', 'elasticsearch']:
            sync_to_elasticsearch(config, mode, since)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        print("\n" + "="*60)
        print(f"  ✓ 所有同步任务完成！总耗时: {elapsed:.2f} 秒")
        print("="*60)
        
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断同步")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 同步失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
