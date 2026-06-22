"""
MySQL到Elasticsearch同步脚本 - CLI入口
支持全量同步和增量同步
"""

import sys
import argparse
from datetime import datetime
from typing import Optional

from config import get_config, ElasticsearchConfig
from sync_manager import ESSyncManager


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='MySQL到Elasticsearch数据同步工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全量同步所有实体
  python sync_from_mysql.py --mode full
  
  # 增量同步（从上次同步时间开始）
  python sync_from_mysql.py --mode incremental --since "2024-12-01 00:00:00"
  
  # 只同步论文数据
  python sync_from_mysql.py --mode full --entity-type work
  
  # 初始化索引（会删除现有数据）
  python sync_from_mysql.py --init-indices --force
  
  # 使用自定义配置
  python sync_from_mysql.py --mode full --es-hosts localhost:9200,localhost:9201
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
        choices=['work', 'author', 'venue', 'institution'],
        help='只同步指定的实体类型（可选）'
    )
    
    parser.add_argument(
        '--init-indices',
        action='store_true',
        help='初始化索引（创建索引和映射）'
    )
    
    parser.add_argument(
        '--force',
        action='store_true',
        help='与--init-indices一起使用，强制重建索引（会删除现有数据）'
    )
    
    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='批量同步的批次大小 (默认: 1000)'
    )
    
    # Elasticsearch连接参数
    parser.add_argument(
        '--es-hosts',
        type=str,
        help='Elasticsearch主机列表（逗号分隔），如: localhost:9200,192.168.1.10:9200'
    )
    parser.add_argument('--es-username', type=str, help='Elasticsearch用户名')
    parser.add_argument('--es-password', type=str, help='Elasticsearch密码')
    
    # MySQL连接参数
    parser.add_argument('--mysql-host', type=str, help='MySQL主机')
    parser.add_argument('--mysql-port', type=int, help='MySQL端口')
    parser.add_argument('--mysql-user', type=str, help='MySQL用户名')
    parser.add_argument('--mysql-password', type=str, help='MySQL密码')
    parser.add_argument('--mysql-database', type=str, default='Scientific_Info_db',
                       help='MySQL数据库名')
    
    return parser.parse_args()


def build_es_config(args) -> ElasticsearchConfig:
    """根据命令行参数构建Elasticsearch配置"""
    config = get_config()
    
    if args.es_hosts:
        config.hosts = [h.strip() for h in args.es_hosts.split(',')]
    if args.es_username:
        config.username = args.es_username
    if args.es_password:
        config.password = args.es_password
    
    return config


def build_mysql_config(args) -> Optional[dict]:
    """根据命令行参数构建MySQL配置"""
    if not any([args.mysql_host, args.mysql_port, args.mysql_user, args.mysql_password]):
        return None
    
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
    es_config = build_es_config(args)
    mysql_config = build_mysql_config(args)
    
    # 创建同步管理器
    try:
        with ESSyncManager(es_config, mysql_config) as sync_manager:
            # 设置批次大小
            sync_manager.batch_size = args.batch_size
            
            # 初始化索引
            if args.init_indices:
                sync_manager.initialize_indices(force_recreate=args.force)
                if not args.mode:  # 仅初始化，不同步
                    print("\n✓ 索引初始化完成!")
                    return
            
            # 解析增量同步时间
            last_sync_time = None
            if args.mode == 'incremental':
                if args.since:
                    last_sync_time = parse_datetime(args.since)
                else:
                    print("✗ 增量同步模式需要指定 --since 参数")
                    sys.exit(1)
            
            # 执行同步
            incremental = (args.mode == 'incremental')
            
            if args.entity_type:
                # 只同步特定实体
                sync_manager.sync_entity(args.entity_type, incremental, last_sync_time)
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
