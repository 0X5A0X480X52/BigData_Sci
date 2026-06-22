"""
MySQL到Neo4j同步脚本 - CLI入口
支持全量同步和增量同步
"""

import sys
import argparse
from datetime import datetime
from typing import Optional

from config import get_config, Neo4jConfig
from sync_manager import Neo4jSyncManager


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='MySQL到Neo4j数据同步工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全量同步
  python sync_from_mysql.py --mode full
  
  # 增量同步（从上次同步时间开始）
  python sync_from_mysql.py --mode incremental --since "2024-12-01 00:00:00"
  
  # 只同步特定节点类型
  python sync_from_mysql.py --mode full --entity-type work
  
  # 只同步特定关系类型
  python sync_from_mysql.py --mode full --relationship-type cites
  
  # 使用自定义配置
  python sync_from_mysql.py --mode full --neo4j-uri bolt://192.168.1.100:7687
        """
    )
    
    parser.add_argument(
        '--mode',
        choices=['full', 'incremental'],
        default='full',
        help='同步模式: full=全量同步, incremental=增量同步 (默认: full)'
    )
    
    parser.add_argument(
        '--since',
        type=str,
        help='增量同步起始时间 (格式: YYYY-MM-DD HH:MM:SS)'
    )
    
    parser.add_argument(
        '--entity-type',
        type=str,
        choices=['author', 'work', 'institution', 'venue', 'concept', 
                 'country', 'database', 'work_type'],
        help='只同步指定的节点类型（可选）'
    )
    
    parser.add_argument(
        '--relationship-type',
        type=str,
        choices=['authored', 'cites', 'published_in', 'about', 
                 'works_at', 'located_in', 'affiliated_with', 'has_type'],
        help='只同步指定的关系类型（可选）'
    )
    
    parser.add_argument(
        '--batch-size',
        type=int,
        default=500,
        help='批量同步的批次大小 (默认: 500)'
    )
    
    # Neo4j连接参数
    parser.add_argument('--neo4j-uri', type=str, help='Neo4j URI')
    parser.add_argument('--neo4j-user', type=str, help='Neo4j用户名')
    parser.add_argument('--neo4j-password', type=str, help='Neo4j密码')
    parser.add_argument('--neo4j-database', type=str, help='Neo4j数据库名')
    
    # MySQL连接参数
    parser.add_argument('--mysql-host', type=str, help='MySQL主机')
    parser.add_argument('--mysql-port', type=int, help='MySQL端口')
    parser.add_argument('--mysql-user', type=str, help='MySQL用户名')
    parser.add_argument('--mysql-password', type=str, help='MySQL密码')
    parser.add_argument('--mysql-database', type=str, default='Scientific_Info_db', 
                       help='MySQL数据库名')
    
    return parser.parse_args()


def build_neo4j_config(args) -> Neo4jConfig:
    """根据命令行参数构建Neo4j配置"""
    config = get_config()  # 先获取默认配置
    
    # 覆盖命令行参数
    if args.neo4j_uri:
        config.uri = args.neo4j_uri
    if args.neo4j_user:
        config.username = args.neo4j_user
    if args.neo4j_password:
        config.password = args.neo4j_password
    if args.neo4j_database:
        config.database = args.neo4j_database
    
    return config


def build_mysql_config(args) -> Optional[dict]:
    """根据命令行参数构建MySQL配置"""
    if not any([args.mysql_host, args.mysql_port, args.mysql_user, args.mysql_password]):
        return None  # 使用默认配置
    
    return {
        'host': args.mysql_host or 'localhost',
        'port': args.mysql_port or 3306,
        'user': args.mysql_user or 'root',
        'password': args.mysql_password or '',
        'database': args.mysql_database
    }


def parse_datetime(datetime_str: str) -> datetime:
    """解析日期时间字符串"""
    try:
        return datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        print(f"✗ 日期格式错误: {datetime_str}")
        print("  正确格式: YYYY-MM-DD HH:MM:SS")
        sys.exit(1)


def main():
    """主函数"""
    args = parse_args()
    
    # 构建配置
    neo4j_config = build_neo4j_config(args)
    mysql_config = build_mysql_config(args)
    
    # 解析增量同步时间
    last_sync_time = None
    if args.mode == 'incremental':
        if args.since:
            last_sync_time = parse_datetime(args.since)
        else:
            print("✗ 增量同步模式需要指定 --since 参数")
            sys.exit(1)
    
    # 创建同步管理器
    try:
        with Neo4jSyncManager(neo4j_config, mysql_config) as sync_manager:
            # 设置批次大小
            sync_manager.batch_size = args.batch_size
            
            # 执行同步
            incremental = (args.mode == 'incremental')
            
            if args.entity_type:
                # 只同步特定节点
                sync_manager.sync_nodes(args.entity_type, incremental, last_sync_time)
            elif args.relationship_type:
                # 只同步特定关系
                sync_manager.sync_relationships(args.relationship_type, incremental, last_sync_time)
            else:
                # 完整同步
                sync_manager.sync_all(incremental, last_sync_time)
            
            print("\n✓ 同步完成!")
            
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
