import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from python_backend.module_data_sync.ES_db.indexer import DocumentIndexer
from python_backend.common.DBConnector.MySQL_db import MySQLConnection
import yaml

with open(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')), 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)['mysql']

mysql_conn = MySQLConnection(**cfg)
indexer = DocumentIndexer(mysql_conn)

def test_work_authors_is_corresponding_boolean():
    docs = indexer.build_work_documents(work_ids=[1])
    assert docs, "No docs built for work_id=1"
    first = docs[0]
    assert 'authors' in first and len(first['authors']) > 0
    assert isinstance(first['authors'][0].get('is_corresponding'), bool)


def test_work_venues_is_primary_boolean():
    docs = indexer.build_work_documents(work_ids=[1])
    assert docs, "No docs built for work_id=1"
    first = docs[0]
    if 'venues' in first and len(first['venues']) > 0:
        assert isinstance(first['venues'][0].get('is_primary'), bool)
