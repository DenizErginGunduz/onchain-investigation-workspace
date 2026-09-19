import copy
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from test_bundles import payload, record, alter, W, OTHER, TX
from app.bundles import import_bundle
from app.settlement import COLLATERAL
from app.storage import CaseStore
from app.trace_case import package_trace, requests, validate_plan
from app.tracing import replay_trace, log_filter


def trace_bundle():
    p = payload(); p['bundle_version'] = '1.2'
    e = p['evidence'][0]
    alter(e['receipt'], lambda b: b['result']['logs'][0].update(address=COLLATERAL))
    log = json.loads(e['receipt']['body_utf8'])['result']['logs'][0]
    start = json.loads(e['block']['body_utf8'])['result']
    end = dict(start, number='0x6e', timestamp=hex(int(start['timestamp'], 16) + 100))
    scope = {'chain': 'eip155:137', 'subject': W, 'contract': COLLATERAL,
             'from_block': 100, 'to_block': 110, 'recipient': OTHER}
    p['trace'] = {'trace_version': '1.0', 'scope': scope, 'chain': e['chain'], 'head': e['head'],
                  'start_block': e['block'], 'end_block': record('eth_getBlockByNumber', ['0x6e', False], end),
                  'queries': [record('eth_getLogs', [log_filter(scope, W, 'incoming')], []),
                              record('eth_getLogs', [log_filter(scope, W, 'outgoing')], [log]),
                              record('eth_getLogs', [log_filter(scope, OTHER, 'outgoing')], [])]}
    return p


def checked(p):
    _, snapshot, receipts = import_bundle(p)
    return replay_trace(snapshot['trace_evidence'], snapshot, receipts)


class TraceTests(unittest.TestCase):
    def test_offline_path_keeps_source_activity_separate_and_preserves_integer(self):
        p = trace_bundle()
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
            result = checked(p)
        self.assertEqual(len(p['snapshot']['events']), 1)
        self.assertEqual(result['events'][0]['amount_raw'], str(10**30 + 7))
        self.assertNotEqual(result['events'][0]['id'], import_bundle(p)[1]['events'][0]['id'])
        self.assertEqual(result['continuation']['state'], 'not_observed_in_window')
        self.assertEqual(result['queries'][0]['returned_logs'], 0)
        self.assertFalse(import_bundle(p)[1]['source']['export_allowed'])

    def test_outside_window_wrong_asset_and_missing_anchor_fail(self):
        for changes in ({'from_block': 0, 'to_block': 1000}, {'contract': OTHER}, {'recipient': W}):
            p = trace_bundle(); p['trace']['scope'].update(changes)
            with self.assertRaises(ValueError): checked(p)
        p = trace_bundle()
        alter(p['trace']['queries'][1], lambda b: b['result'][0].update(blockNumber='0x6f'))
        with self.assertRaises(ValueError): checked(p)

    def test_rehashed_discovery_receipt_conflict_rejected(self):
        p = trace_bundle()
        alter(p['trace']['queries'][1], lambda b: b['result'][0].update(data='0x' + '0'*63 + '1'))
        with self.assertRaises(ValueError): checked(p)

    def test_duplicate_logs_and_filter_mismatch_rejected(self):
        p = trace_bundle()
        alter(p['trace']['queries'][1], lambda b: b['result'].append(copy.deepcopy(b['result'][0])))
        with self.assertRaises(ValueError): checked(p)
        p = trace_bundle(); p['trace']['queries'].reverse()
        with self.assertRaises(ValueError): checked(p)

    def test_removed_topics_and_boundary_hash_mismatch_rejected(self):
        for changes in ({'removed': True}, {'topics': [None]}, {'blockHash': TX}):
            p = trace_bundle()
            alter(p['trace']['queries'][1], lambda b: b['result'][0].update(changes))
            with self.assertRaises(ValueError): checked(p)

    def test_later_counterparty_log_is_candidate_not_proven_fund_continuation(self):
        p = trace_bundle(); q = p['trace']['queries']
        log = json.loads(q[1]['body_utf8'])['result'][0]
        log.update(topics=[log['topics'][0], '0x'+'0'*24+OTHER[2:], '0x'+'0'*24+W[2:]], logIndex='0x8')
        alter(q[0], lambda b: b.update(result=[log]))
        alter(q[2], lambda b: b.update(result=[log]))
        r = checked(p)
        self.assertEqual(r['continuation']['later_outgoing_logs'], 1)
        self.assertEqual(r['continuation']['state'], 'candidates_unverified')
        self.assertEqual(len(r['events']), 1)
        self.assertEqual(r['discovered_unique_logs'], 2)  # Same log in two filters is one identity.

    def test_result_cap_is_visible_and_excess_fails(self):
        p = trace_bundle(); q = p['trace']['queries'][1]
        original = json.loads(q['body_utf8'])['result'][0]
        logs = [original] + [dict(original, transactionHash='0x'+format(i, '064x')) for i in range(1, 500)]
        alter(q, lambda b: b.update(result=logs))
        self.assertTrue(checked(p)['queries'][1]['at_result_cap'])
        alter(q, lambda b: b['result'].append(dict(original, transactionHash='0x'+format(500, '064x'))))
        with self.assertRaises(ValueError): checked(p)

    def test_trace_reopens_and_failure_leaves_existing_case_unchanged(self):
        root = Path(__file__).resolve().parents[1]/'.runtime'; root.mkdir(exist_ok=True)
        path = root/('test-trace-'+uuid.uuid4().hex+'.sqlite3')
        try:
            store = CaseStore(path); old = store.create('Original', payload()['snapshot'])
            store.note(old['id'], 'Keep original history', 'More information needed')
            bad = trace_bundle(); bad['trace']['queries'][0]['sha256'] = 'bad'
            with self.assertRaises(ValueError): store.import_case(bad)
            self.assertEqual(len(store.list()), 1)
            case = store.import_case(trace_bundle())
            self.assertEqual(len(CaseStore(path).get(case['id'])['trace_review']['events']), 1)
            self.assertEqual(store.get(old['id'])['notes'][0]['text'], 'Keep original history')
        finally:
            path.unlink(missing_ok=True)

    def test_archived_packaging_and_invalid_live_plan(self):
        p = trace_bundle(); scope = p['trace']['scope']
        records = [p['trace'][k] for k in ('chain', 'head', 'start_block', 'end_block')] + p['trace']['queries']
        base = copy.deepcopy(p); base['bundle_version'] = '1.1'; base.pop('trace')
        plan = validate_plan(base, 100, 110, OTHER)
        self.assertEqual(len(requests(plan)), 7)
        self.assertEqual(checked(package_trace(base, scope, records))['selected_transfer_count'], 1)
        with self.assertRaises(ValueError): validate_plan(base, 0, 2000, OTHER)
        with self.assertRaises(ValueError): validate_plan(base, 100, 110, W)

    def test_old_bundle_cannot_smuggle_trace_metadata(self):
        p = payload(); p['snapshot']['trace_evidence'] = {'made_up': True}; p['snapshot']['trace_review'] = {'verified': True}
        snapshot = import_bundle(p)[1]
        self.assertNotIn('trace_evidence', snapshot)
        self.assertNotIn('trace_review', snapshot)
