"""
Neo4j连接测试脚本
验证Neo4j连接是否正常，并显示数据库基本信息
"""

import sys
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

from config import get_config


def test_connection():
    """测试Neo4j连接"""
    config = get_config()
    
    print("="*60)
    print("  Neo4j 连接测试")
    print("="*60)
    print(f"\n连接信息:")
    print(f"  URI: {config.uri}")
    print(f"  Username: {config.username}")
    print(f"  Database: {config.database}")
    print()
    
    try:
        # 建立连接
        driver = GraphDatabase.driver(
            config.uri,
            auth=(config.username, config.password)
        )
        
        # 验证连接
        driver.verify_connectivity()
        print("✓ 连接成功!")
        
        # 获取数据库信息
        with driver.session(database=config.database) as session:
            # 查询节点统计
            result = session.run("""
                MATCH (n)
                RETURN labels(n)[0] as label, count(n) as count
                ORDER BY count DESC
            """)
            
            print("\n当前数据库节点统计:")
            total_nodes = 0
            for record in result:
                label = record['label'] or 'Unknown'
                count = record['count']
                total_nodes += count
                print(f"  {label}: {count}")
            
            if total_nodes == 0:
                print("  (数据库为空)")
            else:
                print(f"\n  总计: {total_nodes} 个节点")
            
            # 查询关系统计
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as count
                ORDER BY count DESC
            """)
            
            print("\n当前数据库关系统计:")
            total_rels = 0
            for record in result:
                rel_type = record['rel_type']
                count = record['count']
                total_rels += count
                print(f"  {rel_type}: {count}")
            
            if total_rels == 0:
                print("  (无关系)")
            else:
                print(f"\n  总计: {total_rels} 个关系")
        
        driver.close()
        print("\n" + "="*60)
        print("  测试完成 ✓")
        print("="*60)
        return True
        
    except ServiceUnavailable:
        print("✗ 连接失败: 无法连接到Neo4j服务器")
        print("  请检查:")
        print("  1. Neo4j服务是否已启动")
        print("  2. URI地址是否正确")
        print("  3. 网络连接是否正常")
        return False
        
    except AuthError:
        print("✗ 认证失败: 用户名或密码错误")
        print("  请检查配置文件或环境变量中的认证信息")
        return False
        
    except Exception as e:
        print(f"✗ 未知错误: {e}")
        return False


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
