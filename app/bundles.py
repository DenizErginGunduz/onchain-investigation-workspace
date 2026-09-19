"""Offline validation/replay of imported evidence. Never contacts an imported URL."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from urllib.parse import urlsplit

from .domain import validate_snapshot
from .reconciliation import HASH, ADDRESS, reconcile, quantity
from .review_case import finality_check, abi_uint, abi_string
from .settlement import simple_buy, COLLATERAL, EXCHANGE


def record_result(record, method, params, source=None):
    if not isinstance(record, dict):
        raise ValueError('Missing raw RPC record')
    url = record.get('url')
    parsed = urlsplit(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('RPC source must be an HTTPS URL without credentials or query parameters')
    if source is not None and url != source:
        raise ValueError('Mixed RPC sources in one evidence set')
    request = record.get('request')
    if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or type(request.get('id')) is not int or request.get('method') != method or request.get('params') != params:
        raise ValueError('RPC request does not match its declared evidence role')
    timestamp = record.get('retrieved_at')
    if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('RPC retrieval time must include timezone')
    raw = record.get('body_utf8')
    if not isinstance(raw, str) or len(raw.encode()) > 1_000_000 or sha256(raw.encode()).hexdigest() != record.get('sha256'):
        raise ValueError('RPC response integrity check failed')
    if record.get('tls_verification') is not True or record.get('http_status') != 200:
        raise ValueError('RPC record does not declare successful verified-TLS acquisition')
    def invalid_constant(value): raise ValueError('Non-finite JSON value')
    body = json.loads(raw, parse_constant=invalid_constant)
    if not isinstance(body, dict) or body.get('jsonrpc') != '2.0' or type(body.get('id')) is not int or body['id'] != request['id'] or 'result' not in body or 'error' in body:
        raise ValueError('Invalid or failed RPC response')
    return body['result']


def _replay(evidence, snapshot):
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
        raise ValueError('A bundle needs one to three receipt evidence sets')
    if snapshot['subject']['chain'] != 'eip155:137' or not ADDRESS.fullmatch(snapshot['subject']['address']):
        raise ValueError('Receipt replay currently supports a Polygon EVM subject')
    transactions = {e['tx'].lower() for e in snapshot['events'] if e['chain'] == 'eip155:137'}
    results, seen = [], set()
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get('tx'), str) or not HASH.fullmatch(item['tx']):
            raise ValueError('Invalid receipt transaction')
        tx = item['tx'].lower()
        if tx in seen or tx not in transactions:
            raise ValueError('Duplicate receipt or transaction outside snapshot coverage')
        seen.add(tx)
        receipt = record_result(item.get('receipt'), 'eth_getTransactionReceipt', [tx])
        source = item['receipt']['url']
        transaction = record_result(item.get('transaction'), 'eth_getTransactionByHash', [tx], source)
        chain = record_result(item.get('chain'), 'eth_chainId', [], source)
        head = record_result(item.get('head'), 'eth_blockNumber', [], source)
        block = record_result(item.get('block'), 'eth_getBlockByNumber', [receipt['blockNumber'], False], source) if receipt else None
        checked = reconcile(tx, snapshot['subject']['address'], receipt, transaction, block, head, chain)
        result = {'tx': tx, 'receipt': checked, 'source': source, 'retrieved_at': item['receipt']['retrieved_at'],
                  'finality': {'state': 'unresolved', 'reason': 'No finalized-tag evidence included'},
                  'settlement': {'state': 'unresolved', 'reason': 'No supported successful settlement'},
                  'metadata': []}
        if checked['state'] == 'rpc_corroborated':
            if 'finalized' in item or 'finalized_canonical' in item:
                finalized = record_result(item.get('finalized'), 'eth_getBlockByNumber', ['finalized', False], source)
                canonical = record_result(item.get('finalized_canonical'), 'eth_getBlockByNumber', [finalized['number'], False], source)
                # Same imported block is the baseline: no claim about earlier live acquisition.
                result['finality'] = finality_check(checked, checked, finalized, canonical, head)
                result['finality'].pop('original_block_unchanged', None)
            if checked['execution'] == 'success' and isinstance(transaction.get('to'), str) and transaction['to'].lower() == EXCHANGE:
                result['settlement'] = simple_buy(receipt, snapshot['subject']['address'])
            metadata = item.get('metadata', [])
            if not isinstance(metadata, list) or len(metadata) > 3:
                raise ValueError('At most three collateral metadata calls are supported')
            selectors = {'decimals': ('0x313ce567', abi_uint), 'name': ('0x06fdde03', abi_string), 'symbol': ('0x95d89b41', abi_string)}
            names = set()
            for meta in metadata:
                name = meta.get('field')
                if name not in selectors or name in names:
                    raise ValueError('Unknown or repeated metadata field')
                names.add(name)
                tag = meta['block']
                allowed_tags = {hex(checked['block_number'])}
                if result['finality'].get('finalized_block_number') is not None:
                    allowed_tags.add(hex(result['finality']['finalized_block_number']))
                if tag not in allowed_tags:
                    raise ValueError('Metadata block is outside the included evidence')
                selector, decoder = selectors[name]
                raw = record_result(meta['record'], 'eth_call', [{'to': COLLATERAL, 'data': selector}, tag], source)
                value = decoder(raw)
                if name == 'decimals' and not 0 <= value <= 36:
                    raise ValueError('Unsupported display precision')
                result['metadata'].append({'field': name, 'value': value, 'block_number': quantity(tag),
                    'at_receipt_block': quantity(tag) == checked['block_number'], 'contract': COLLATERAL})
        results.append(result)
    return {'mode': 'offline_replay', 'checks': results,
            'notice': 'Recomputed from imported RPC responses. Hashes check file integrity, not source authenticity. No live query or independent consensus proof.',
            'scope': 'Selected transactions only. Provider amounts, ownership and intent remain separate assertions.'}


def replay_evidence(evidence, snapshot):
    try:
        return _replay(evidence, snapshot)
    except (KeyError, TypeError, AttributeError, OverflowError, IndexError) as exc:
        raise ValueError('Malformed receipt evidence; nothing was imported') from exc


def import_bundle(payload):
    if not isinstance(payload, dict) or payload.get('bundle_version') not in ('1.1', '1.2'):
        raise ValueError('A replayable case bundle version 1.1 or 1.2 is required; use the package-review command for archived evidence')
    title = payload.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        raise ValueError('Bundle title must contain 1–160 characters')
    snapshot = validate_snapshot(payload.get('snapshot'))
    # Imported summaries never grant trust or replace recomputed results.
    for key in ('receipt_review', 'receipt_evidence', 'bundle_import', 'trace_evidence', 'trace_review'):
        snapshot.pop(key, None)
    evidence = deepcopy(payload.get('evidence'))
    review = replay_evidence(evidence, snapshot)
    if payload['bundle_version'] == '1.2':
        from .tracing import replay_trace
        trace = deepcopy(payload.get('trace'))
        replay_trace(trace, snapshot, review)
        snapshot['trace_evidence'] = trace
    snapshot['receipt_evidence'] = evidence
    snapshot['bundle_import'] = {'version': payload['bundle_version'], 'export_restricted': True}
    # Real acquisition rights cannot be elevated through an uploaded boolean.
    snapshot['source']['export_allowed'] = False
    return title.strip(), snapshot, review
