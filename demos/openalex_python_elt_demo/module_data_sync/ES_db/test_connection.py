"""
Elasticsearch连接测试脚本
验证ES连接是否正常，并显示集群基本信息
"""

import sys
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException

from config import get_config


def test_connection():
    """测试Elasticsearch连接"""
    config = get_config()
    
    print("="*60)
    print("  Elasticsearch 连接测试")
    print("="*60)
    print(f"\n连接信息:")
    print(f"  Hosts: {config.hosts}")
    if config.username:
        print(f"  Username: {config.username}")
    print()
    
    try:
        # 建立连接
        if config.username and config.password:
            es = Elasticsearch(
                config.hosts,
                basic_auth=(config.username, config.password),
                verify_certs=config.verify_certs
            )
        else:
            es = Elasticsearch(
                config.hosts,
                verify_certs=config.verify_certs
            )
        
        # 验证连接
        if not es.ping():
            print("✗ 连接失败: 无法ping通Elasticsearch服务器")
            return False
        
        print("✓ 连接成功!")
        
        # 获取集群信息
        info = es.info()
        print(f"\n集群信息:")
        print(f"  名称: {info['cluster_name']}")
        print(f"  版本: {info['version']['number']}")
        
        # 获取集群健康状态
        health = es.cluster.health()
        print(f"\n集群健康:")
        print(f"  状态: {health['status']}")
        print(f"  节点数: {health['number_of_nodes']}")
        print(f"  数据节点数: {health['number_of_data_nodes']}")
        
        # 列出所有索引
        indices = es.cat.indices(format='json')
        print(f"\n当前索引 ({len(indices)} 个):")
        
        if indices:
            for idx in indices:
                print(f"  - {idx['index']}: {idx['docs.count']} 文档, {idx['store.size']}")
        else:
            print("  (无索引)")
        
        es.close()
        
        print("\n" + "="*60)
        print("  测试完成 ✓")
        print("="*60)
        return True
        
    except ConnectionError:
        print("✗ 连接失败: 无法连接到Elasticsearch服务器")
        print("  请检查:")
        print("  1. Elasticsearch服务是否已启动")
        print("  2. Hosts地址是否正确")
        print("  3. 网络连接是否正常")
        return False
        
    except AuthenticationException:
        print("✗ 认证失败: 用户名或密码错误")
        print("  请检查配置文件或环境变量中的认证信息")
        return False
        
    except Exception as e:
        print(f"✗ 未知错误: {e}")
        return False


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
