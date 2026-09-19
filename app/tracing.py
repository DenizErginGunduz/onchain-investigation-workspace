"""Bounded collateral-log discovery and selected receipt-backed transfer paths."""
from datetime import datetime, timezone

from .bundles import record_result
from .domain import evidence_id
from .reconciliation import ADDRESS, HASH, TRANSFER, quantity
from .settlement import COLLATERAL

ZERO = '0x' + '0' * 40
MAX_BLOCKS = 1000
MAX_LOGS = 500


def topic(address):
    return '0x' + '0' * 24 + address[2:]


def log_filter(scope, address, direction):
    topics = [TRANSFER, topic(address)] if direction == 'outgoing' else [TRANSFER, None, topic(address)]
    return {'address': scope['contract'], 'fromBlock': hex(scope['from_block']),
            'toBlock': hex(scope['to_block']), 'topics': topics}


def parse_log(log, scope, address, direction):
    if not isinstance(log, dict) or log.get('removed') is not False:
        raise ValueError('Removed or malformed discovery log')
    topics = log.get('topics')
    if not isinstance(topics, list) or len(topics) != 3 or topics[0] != TRANSFER:
        raise ValueError('Expected collateral Transfer log')
    if not all(isinstance(t, str) and len(t) == 66 and t.startswith('0x' + '0' * 24) and HASH.fullmatch(t) for t in topics[1:]):
        raise ValueError('Invalid transfer address topic')
    for field in ('transactionHash', 'blockHash', 'data'):
        if not isinstance(log.get(field), str) or not HASH.fullmatch(log[field]):
            raise ValueError('Invalid discovery hash or amount')
    if log.get('address', '').lower() != scope['contract']:
        raise ValueError('Discovery contract outside scope')
    sender, recipient = ('0x' + t[-40:].lower() for t in topics[1:])
    if (sender if direction == 'outgoing' else recipient) != address:
        raise ValueError('Discovery log does not match address filter')
    block = quantity(log.get('blockNumber'))
    if not scope['from_block'] <= block <= scope['to_block']:
        raise ValueError('Discovery log outside block window')
    return {'tx': log['transactionHash'].lower(), 'locator': 'log:' + str(quantity(log.get('logIndex'))),
            'block_number': block, 'block_hash': log['blockHash'].lower(),
            'transaction_index': quantity(log.get('transactionIndex')), 'from': sender, 'to': recipient,
            'contract': scope['contract'], 'amount_raw': str(int(log['data'], 16))}


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _replay(trace, snapshot, receipt_review):
    if not isinstance(trace, dict) or trace.get('trace_version') != '1.0':
        raise ValueError('Expected trace evidence version 1.0')
    scope = trace['scope']
    if not isinstance(scope, dict) or scope.get('contract') != COLLATERAL or scope.get('chain') != 'eip155:137':
        raise ValueError('Only the declared Polygon collateral contract is supported')
    wallet = snapshot['subject']['address'].lower()
    if snapshot['subject']['chain'] != scope['chain'] or scope.get('subject') != wallet:
        raise ValueError('Trace subject differs from case')
    low, high = scope['from_block'], scope['to_block']
    if type(low) is not int or type(high) is not int or low < 0 or not 1 <= high - low + 1 <= MAX_BLOCKS:
        raise ValueError('Trace window must contain 1–1000 blocks')
    recipient = scope.get('recipient')
    if not isinstance(recipient, str) or not ADDRESS.fullmatch(recipient) or recipient != recipient.lower() or recipient in (wallet, ZERO):
        raise ValueError('Invalid selected recipient')
    chain = record_result(trace['chain'], 'eth_chainId', [])
    source = trace['chain']['url']
    if quantity(chain) != 137:
        raise ValueError('Trace source is not Polygon')
    head = quantity(record_result(trace['head'], 'eth_blockNumber', [], source))
    if head < high:
        raise ValueError('Trace window is ahead of source head')
    bounds = []
    for key, number in [('start_block', low), ('end_block', high)]:
        block = record_result(trace[key], 'eth_getBlockByNumber', [hex(number), False], source)
        if quantity(block['number']) != number or not HASH.fullmatch(block['hash']):
            raise ValueError('Invalid window boundary block')
        bounds.append({'number': number, 'hash': block['hash'].lower(), 'timestamp': utc(quantity(block['timestamp']))})
    if bounds[0]['timestamp'] > bounds[1]['timestamp']:
        raise ValueError('Window boundary timestamps are reversed')
    queries = trace['queries']
    roles = [(wallet, 'incoming'), (wallet, 'outgoing'), (recipient, 'outgoing')]
    if not isinstance(queries, list) or len(queries) != len(roles):
        raise ValueError('Trace requires subject incoming/outgoing and selected recipient outgoing queries')
    all_logs, summaries, role_logs = {}, [], []
    for query, (address, direction) in zip(queries, roles):
        logs = record_result(query, 'eth_getLogs', [log_filter(scope, address, direction)], source)
        if not isinstance(logs, list) or len(logs) > MAX_LOGS:
            raise ValueError('Discovery result exceeds 500-log budget')
        seen, parsed = set(), []
        for log in logs:
            item = parse_log(log, scope, address, direction)
            if any(item['block_number'] == b['number'] and item['block_hash'] != b['hash'] for b in bounds):
                raise ValueError('Discovery log conflicts with boundary block')
            key = (item['tx'], item['locator'])
            if key in seen or (key in all_logs and all_logs[key] != item):
                raise ValueError('Duplicate or conflicting discovery identity')
            seen.add(key); all_logs[key] = item; parsed.append(item)
        role_logs.append(parsed)
        summaries.append({'address': address, 'direction': direction, 'returned_logs': len(logs),
                          'at_result_cap': len(logs) == MAX_LOGS, 'retrieved_at': query['retrieved_at']})
    events = []
    for check in receipt_review['checks']:
        receipt = check['receipt']
        if check['source'] != source:
            raise ValueError('Selected receipt and discovery sources differ')
        if receipt.get('state') != 'rpc_corroborated' or receipt.get('execution') != 'success':
            continue
        if not low <= receipt['block_number'] <= high:
            continue
        if not bounds[0]['timestamp'] <= utc(receipt['block_timestamp']) <= bounds[1]['timestamp']:
            raise ValueError('Receipt timestamp outside window boundaries')
        for transfer in receipt['transfers']:
            if transfer['contract'] != scope['contract']:
                continue
            key = (check['tx'], transfer['locator'])
            discovered = all_logs.get(key)
            expected = {k: transfer[k] for k in ('locator', 'contract', 'from', 'to', 'amount_raw')}
            expected.update(tx=check['tx'], block_number=receipt['block_number'], block_hash=receipt['block_hash'],
                            transaction_index=receipt['transaction_index'])
            if discovered != expected:
                raise ValueError('Selected receipt transfer is absent from or conflicts with discovery logs')
            direction = 'Self-transfer' if transfer['from'] == transfer['to'] else 'Outgoing' if transfer['from'] == wallet else 'Incoming'
            event = {**expected, 'chain': scope['chain'], 'timestamp': utc(receipt['block_timestamp']),
                     'label': direction + ' collateral transfer', 'kind': 'transfer', 'status': 'observed',
                     'asset': {'id': scope['chain'] + '/erc20:' + scope['contract'], 'decimals': 0,
                               'symbol': 'raw units', 'display_basis': 'Raw integer display; token decimals not asserted'},
                     'amount_display': transfer['amount_raw'], 'source': source,
                     'evidence_basis': 'Imported RPC log matched to selected receipt and canonical block position'}
            event['id'] = evidence_id(event); events.append(event)
    anchors = [e for e in events if e['from'] == wallet and e['to'] == recipient and int(e['amount_raw']) > 0]
    if not anchors:
        raise ValueError('Selected recipient must receive a positive receipt-backed transfer from the subject')
    first = min(anchors, key=lambda e: (e['block_number'], e['transaction_index'], int(e['locator'][4:])))
    def position(e): return (e['block_number'], e['transaction_index'], int(e['locator'][4:]))
    later = [e for e in role_logs[2] if position(e) > position(first)]
    return {'scope': scope, 'bounds': bounds, 'queries': summaries, 'events': events,
            'discovered_unique_logs': len(all_logs), 'selected_transfer_count': len(events),
            'source': source, 'mode': 'offline_replay',
            'continuation': {'address': recipient, 'after_event_id': first['id'], 'later_outgoing_logs': len(later),
                'state': 'candidates_unverified' if later else 'not_observed_in_window',
                'detail': 'Later recipient logs require separate receipt review; no same-fund continuity is established.' if later else 'No later outgoing log returned for this recipient and contract in this window. Continuation outside this coverage is unresolved.'},
            'limitations': ['Only one collateral contract and the recorded block window were queried; this is not complete wallet history.',
                'Native/internal transfers, other tokens, outcome tokens and other chains were not searched.',
                'Query results are one RPC source assertion, not a proof that every matching log was returned. A full 500-log result is marked at-cap.',
                'Only selected receipt-backed transfers enter the graph and ledger; other returned logs are discovery evidence.',
                'No ownership, service identity, crime finding or attribution of the same funds to later payments is inferred.']}


def replay_trace(trace, snapshot, receipt_review):
    try:
        return _replay(trace, snapshot, receipt_review)
    except (KeyError, TypeError, AttributeError, OverflowError, IndexError) as exc:
        raise ValueError('Malformed bounded trace evidence; nothing was imported') from exc
