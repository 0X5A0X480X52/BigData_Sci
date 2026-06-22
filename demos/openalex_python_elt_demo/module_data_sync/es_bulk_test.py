import yaml
from python_backend.module_data_sync.ES_db.config import ElasticsearchConfig
from python_backend.module_data_sync.ES_db.sync_manager import ESSyncManager

cfg = yaml.safe_load(open('python_backend/module_data_sync/config.yaml', 'r', encoding='utf-8'))
es_conf = ElasticsearchConfig.from_dict(cfg['elasticsearch'])
mysql_cfg = cfg['mysql']

mgr = ESSyncManager(es_conf, mysql_cfg)
try:
    mgr.connect()
    docs = mgr.indexer.build_work_documents(work_ids=[1,2,3])
    print('Built docs:', len(docs))
    if docs:
        actions = []
        for d in docs:
            actions.append({'_index': mgr.INDEX_NAMES['work'], '_id': d.get('work_id'), '_source': mgr._normalize_for_es(d)})
        print('Sample action keys:', list(actions[0].keys()))
        res = mgr._bulk_index(actions)
        print('Bulk result:', res)
    else:
        print('No docs built for sample work_ids')
finally:
    mgr.close()
