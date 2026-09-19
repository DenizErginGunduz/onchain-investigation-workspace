"""Seven-request collateral discovery, or offline packaging of the same records."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from .bundles import import_bundle
from .collect_case import Acquisition
from .reconciliation import ADDRESS
from .settlement import COLLATERAL
from .tracing import log_filter, MAX_BLOCKS


def requests(scope):
    return [('eth_chainId', []), ('eth_blockNumber', []),
            ('eth_getBlockByNumber', [hex(scope['from_block']), False]),
            ('eth_getBlockByNumber', [hex(scope['to_block']), False])] + [
                ('eth_getLogs', [log_filter(scope, address, direction)]) for address, direction in
                [(scope['subject'], 'incoming'), (scope['subject'], 'outgoing'), (scope['recipient'], 'outgoing')]]


def validate_plan(bundle, low, high, recipient):
    _, snapshot, review = import_bundle(bundle)
    if type(low) is not int or type(high) is not int or low < 0 or not 1 <= high - low + 1 <= MAX_BLOCKS:
        raise ValueError('Expected a 1–1000-block window')
    if not isinstance(recipient, str) or not ADDRESS.fullmatch(recipient):
        raise ValueError('Expected an EVM recipient')
    recipient = recipient.lower()
    wallet = snapshot['subject']['address'].lower()
    if recipient in (wallet, '0x' + '0' * 40):
        raise ValueError('Choose a nonzero counterparty distinct from the subject')
    if not any(c['receipt'].get('state') == 'rpc_corroborated' and low <= c['receipt']['block_number'] <= high
               and any(t['from'] == wallet and t['to'] == recipient and t['contract'] == COLLATERAL and int(t['amount_raw']) > 0
                       for t in c['receipt']['transfers']) for c in review['checks']):
        raise ValueError('Recipient must have a selected receipt-backed payment in this window')
    return {'chain': 'eip155:137', 'subject': wallet, 'contract': COLLATERAL,
            'from_block': low, 'to_block': high, 'recipient': recipient}


def package_trace(bundle, scope, records):
    chosen = []
    for method, params in requests(scope):
        matches = [r for r in records if r.get('request', {}).get('method') == method and r['request'].get('params') == params]
        if not matches:
            raise ValueError('Missing archived trace request: ' + method)
        chosen.append(matches[-1])
    output = deepcopy(bundle)
    output.update(bundle_version='1.2', title='Bounded collateral outflow - receipt-backed path',
                  trace={'trace_version': '1.0', 'scope': scope, 'chain': chosen[0], 'head': chosen[1],
                         'start_block': chosen[2], 'end_block': chosen[3], 'queries': chosen[4:]})
    import_bundle(output)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--from-block', type=int, required=True)
    parser.add_argument('--to-block', type=int, required=True)
    parser.add_argument('--recipient', required=True)
    parser.add_argument('--records', help='Existing JSON array of archived records; makes no network requests')
    parser.add_argument('--output', required=True, help='New local output directory')
    args = parser.parse_args()
    bundle = json.loads(Path(args.bundle).read_text(encoding='utf-8'))
    scope = validate_plan(bundle, args.from_block, args.to_block, args.recipient)
    output = Path(args.output); output.mkdir(parents=True, exist_ok=False)
    plan = {'scope': scope, 'rpc_cap': 7, 'response_byte_cap': 1000000, 'query_log_cap': 500,
            'selected_receipt_cap': 3, 'hop_limit': 2, 'export_allowed': False,
            'mode': 'offline_packaging' if args.records else 'live_discovery',
            'created_at': datetime.now(timezone.utc).isoformat()}
    (output / 'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    acquisition = Acquisition()
    outcome = {'state': 'failed', 'new_rpc_requests': 0}
    try:
        if args.records:
            acquisition.records = json.loads(Path(args.records).read_text(encoding='utf-8'))
        else:
            for method, params in requests(scope):
                outcome['new_rpc_requests'] += 1
                acquisition.rpc(method, params)
        packaged = package_trace(bundle, scope, acquisition.records)
        raw = json.dumps(packaged, ensure_ascii=False, separators=(',', ':'))
        if len(raw.encode()) > 900000:
            raise ValueError('Trace bundle exceeds 900 KB packaging budget; narrow the window in a new run')
        (output / 'case-bundle.json').write_text(raw, encoding='utf-8')
        outcome.update(state='packaged', bytes=len(raw.encode()))
    except (ValueError, TypeError, KeyError) as exc:
        outcome.update(error_type=type(exc).__name__, message=str(exc))
    finally:
        (output / 'acquisition.json').write_text(json.dumps(acquisition.records, indent=2), encoding='utf-8')
        (output / 'outcome.json').write_text(json.dumps(outcome, indent=2), encoding='utf-8')
        manifest = {p.name: sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(outcome))
    return 0 if outcome['state'] == 'packaged' else 1


if __name__ == '__main__':
    raise SystemExit(main())
