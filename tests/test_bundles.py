import copy
from hashlib import sha256
import json
from pathlib import Path
import uuid
import unittest
from unittest.mock import patch

from app.bundles import import_bundle
from app.storage import CaseStore
from app.reconciliation import TRANSFER

TX='0x'+'a'*64
BH='0x'+'b'*64
W='0x'+'1'*40
OTHER='0x'+'2'*40
RPC='https://example.invalid/rpc'


def record(method, params, result):
    raw=json.dumps({'jsonrpc':'2.0','id':1,'result':result})
    return {'url':RPC,'request':{'jsonrpc':'2.0','id':1,'method':method,'params':params},
            'retrieved_at':'2026-09-14T00:00:00Z','http_status':200,'tls_verification':True,
            'body_utf8':raw,'sha256':sha256(raw.encode()).hexdigest()}


def payload():
    log={'transactionHash':TX,'blockHash':BH,'blockNumber':'0x64','transactionIndex':'0x0','logIndex':'0x7','removed':False,
         'address':OTHER,'topics':[TRANSFER,'0x'+'0'*24+W[2:],'0x'+'0'*24+OTHER[2:]],'data':'0x'+format(10**30+7,'064x')}
    receipt={'transactionHash':TX,'blockHash':BH,'blockNumber':'0x64','transactionIndex':'0x0','status':'0x1','logs':[log]}
    tx={'hash':TX,'blockHash':BH,'blockNumber':'0x64','transactionIndex':'0x0','to':OTHER}
    block={'number':'0x64','hash':BH,'timestamp':'0x65000000','transactions':[TX]}
    evidence={'tx':TX,'receipt':record('eth_getTransactionReceipt',[TX],receipt),
              'transaction':record('eth_getTransactionByHash',[TX],tx),'block':record('eth_getBlockByNumber',['0x64',False],block),
              'head':record('eth_blockNumber',[],'0x80'),'chain':record('eth_chainId',[],'0x89')}
    snapshot={'schema_version':'1.0','synthetic':True,'subject':{'chain':'eip155:137','address':W},
              'source':{'provider':'Authored test fixture','retrieved_at':'2026-09-14T00:00:00Z','reference':'authored fixture','export_allowed':True},
              'coverage':{'state':'bounded','limitations':['Fictional test data']},'events':[
                  {'chain':'eip155:137','tx':TX,'locator':'provider-row:1','timestamp':'2026-09-14T00:00:00Z','from':W,'to':'Activity',
                   'label':'Authored observation','kind':'market_activity','status':'provider_reported','asset':{'id':'reported:usd','symbol':'reported USD','decimals':6},'amount_raw':'100'}]}
    return {'bundle_version':'1.1','title':'Authored receipt test','snapshot':snapshot,'evidence':[evidence]}


def alter(record, change):
    body=json.loads(record['body_utf8']);change(body)
    raw=json.dumps(body);record['body_utf8']=raw;record['sha256']=sha256(raw.encode()).hexdigest()


class BundleTests(unittest.TestCase):
    def test_import_replays_without_network_and_does_not_elevate_export_policy(self):
        p=payload()
        p['snapshot']['receipt_review']={'state':'verified','made_up':True}
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            _, snapshot, review=import_bundle(p)
        self.assertFalse(snapshot['source']['export_allowed'])
        self.assertTrue(p['snapshot']['source']['export_allowed'])
        self.assertNotIn('receipt_review',snapshot)
        self.assertEqual(snapshot['events'][0]['status'],'provider_reported')
        self.assertEqual(review['checks'][0]['receipt']['transfers'][0]['amount_raw'],str(10**30+7))
        self.assertEqual(review['checks'][0]['finality']['state'],'unresolved')

    def test_corrupt_digest_is_rejected(self):
        p=payload();p['evidence'][0]['receipt']['sha256']='0'*64
        with self.assertRaises(ValueError):import_bundle(p)

    def test_rehashed_wrong_receipt_block_still_fails(self):
        p=payload();alter(p['evidence'][0]['receipt'],lambda body:body['result'].update(blockHash=TX))
        with self.assertRaises(ValueError):import_bundle(p)

    def test_wrong_request_and_response_ids_fail(self):
        p=payload();p['evidence'][0]['receipt']['request']['params']=[BH]
        with self.assertRaises(ValueError):import_bundle(p)
        p=payload();alter(p['evidence'][0]['head'],lambda body:body.update(id=2))
        with self.assertRaises(ValueError):import_bundle(p)

    def test_foreign_chain_transaction_and_duplicate_sets_fail(self):
        p=payload();p['snapshot']['subject']['chain']='eip155:1'
        with self.assertRaises(ValueError):import_bundle(p)
        p=payload();p['snapshot']['events'][0]['tx']=BH
        with self.assertRaises(ValueError):import_bundle(p)
        p=payload();p['evidence']*=2
        with self.assertRaises(ValueError):import_bundle(p)

    def test_failed_or_mixed_source_records_fail(self):
        p=payload();p['evidence'][0]['head']['url']='https://different.invalid/'
        with self.assertRaises(ValueError):import_bundle(p)
        p=payload();alter(p['evidence'][0]['head'],lambda body:body.update(error={'code':-1}))
        with self.assertRaises(ValueError):import_bundle(p)

    def test_claimed_finality_requires_matching_raw_tag_and_canonical_block(self):
        p=payload();item=p['evidence'][0];block=json.loads(item['block']['body_utf8'])['result']
        item['finalized']=record('eth_getBlockByNumber',['finalized',False],block)
        item['finalized_canonical']=copy.deepcopy(item['block'])
        self.assertEqual(import_bundle(p)[2]['checks'][0]['finality']['state'],'rpc_reported_finalized')
        alter(item['finalized_canonical'],lambda body:body['result'].update(hash=TX))
        with self.assertRaises(ValueError):import_bundle(p)

    def test_unknown_schema_malformed_topics_and_partial_finality_fail_cleanly(self):
        p=payload();p['bundle_version']='1.0'
        with self.assertRaises(ValueError):import_bundle(p)
        p=payload();alter(p['evidence'][0]['receipt'],lambda body:body['result']['logs'][0].update(topics=[None]))
        with self.assertRaises(ValueError):import_bundle(p)
        p=payload();p['evidence'][0]['finalized_canonical']=p['evidence'][0]['block']
        with self.assertRaises(ValueError):import_bundle(p)

    def test_invalid_import_is_atomic_and_reopen_recomputes_receipts(self):
        root = Path(__file__).resolve().parents[1] / '.runtime'
        root.mkdir(exist_ok=True)
        path = root / ('test-bundle-' + uuid.uuid4().hex + '.sqlite3')
        try:
            store=CaseStore(path)
            original=store.create('Original case',payload()['snapshot'])
            store.note(original['id'],'Retain this note','More information needed')
            bad=payload();bad['evidence'][0]['receipt']['sha256']='bad'
            with self.assertRaises(ValueError):store.import_case(bad)
            self.assertEqual(len(store.list()),1)
            imported=store.import_case(payload())
            reopened=store.get(imported['id'])
            self.assertEqual(reopened['receipt_review']['checks'][0]['receipt']['state'],'rpc_corroborated')
            self.assertEqual(store.get(original['id'])['notes'][0]['text'],'Retain this note')
            self.assertFalse(reopened['analysis']['snapshot']['source']['export_allowed'])

        finally:
            path.unlink(missing_ok=True)

class BundleHttpTests(unittest.TestCase):
    def test_http_import_failure_export_guard_and_snapshot_isolation(self):
        from http.server import ThreadingHTTPServer
        from http.client import HTTPConnection
        from threading import Thread
        from server import make_handler
        root=Path(__file__).resolve().parents[1]/'.runtime'
        root.mkdir(exist_ok=True)
        path=root/('test-http-'+uuid.uuid4().hex+'.sqlite3')
        store=CaseStore(path)
        host=ThreadingHTTPServer(('127.0.0.1',0),make_handler(store))
        thread=Thread(target=host.serve_forever,daemon=True);thread.start()
        def request(method,route,data=None,local=True):
            connection=HTTPConnection('127.0.0.1',host.server_port,timeout=5)
            headers={'Content-Type':'application/json'}
            if local:headers['X-Local-Investigation']='1'
            connection.request(method,route,json.dumps(data) if data is not None else None,headers)
            response=connection.getresponse();status=response.status
            body=json.loads(response.read());connection.close()
            return status,body
        try:
            self.assertEqual(request('POST','/api/cases/import-bundle',{'bundle':payload()},False)[0],403)
            status,demo=request('GET','/api/demo-bundle')
            self.assertEqual(status,200)
            self.assertTrue(demo['snapshot']['synthetic'])
            with patch('urllib.request.urlopen',side_effect=AssertionError('Demo must stay offline')):
                self.assertEqual(import_bundle(demo)[2]['checks'][0]['receipt']['state'],'rpc_corroborated')
            self.assertEqual(request('GET','/fixtures/trace-demo.json')[0],404)
            self.assertEqual(request('GET','/.runtime/cases.sqlite3')[0],404)
            self.assertEqual(store.list(),[])
            bad=payload();bad['evidence'][0]['receipt']['sha256']='bad'
            self.assertEqual(request('POST','/api/cases/import-bundle',{'bundle':bad})[0],400)
            self.assertEqual(store.list(),[])
            status,case=request('POST','/api/cases/import-bundle',{'bundle':payload()})
            self.assertEqual(status,201)
            self.assertEqual(request('GET','/api/cases/'+case['id']+'/snapshot')[0],403)
            snapshot=payload()['snapshot'];snapshot['receipt_evidence']=payload()['evidence']
            snapshot['receipt_review']={'state':'fake verified'}
            status,plain=request('POST','/api/cases',{'provider':'snapshot','snapshot':snapshot,'title':'Plain snapshot'})
            self.assertEqual(status,201)
            self.assertIsNone(plain['receipt_review'])
            self.assertNotIn('receipt_evidence',plain['analysis']['snapshot'])
            self.assertEqual(len(store.list()),2)
        finally:
            host.shutdown();host.server_close();thread.join(timeout=5)
            path.unlink(missing_ok=True)
