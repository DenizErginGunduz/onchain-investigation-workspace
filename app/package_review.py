"""Package archived records for local UI import. No network or data redistribution."""
import argparse
import json
from pathlib import Path
from .bundles import import_bundle
from .settlement import COLLATERAL


def package(bundle, records):
    def find(method, params):
        matches = [r for r in records if r.get('request', {}).get('method') == method and r['request'].get('params') == params]
        if not matches:
            raise ValueError('Archived RPC evidence missing: ' + method)
        return matches[-1]
    def result(record): return json.loads(record['body_utf8'])['result']
    evidence = []
    transactions = [c['tx'] for c in bundle['chain_checks']]
    for tx in transactions:
        receipt = find('eth_getTransactionReceipt', [tx])
        item = {'tx': tx, 'receipt': receipt, 'transaction': find('eth_getTransactionByHash', [tx]),
                'block': find('eth_getBlockByNumber', [result(receipt)['blockNumber'], False]),
                'head': find('eth_blockNumber', []), 'chain': find('eth_chainId', [])}
        finalized = [r for r in records if r.get('request', {}).get('params') == ['finalized', False] and r['request']['method'] == 'eth_getBlockByNumber' and 'result' in json.loads(r.get('body_utf8', '{}'))]
        if finalized:
            item['finalized'] = finalized[-1]
            tag = result(finalized[-1])['number']
            item['finalized_canonical'] = find('eth_getBlockByNumber', [tag, False])
            item['metadata'] = []
            for name, selector in [('decimals','0x313ce567'), ('name','0x06fdde03'), ('symbol','0x95d89b41')]:
                candidates = [r for r in records if r.get('request', {}).get('method') == 'eth_call' and r['request']['params'] == [{'to':COLLATERAL, 'data':selector}, tag] and 'result' in json.loads(r.get('body_utf8', '{}'))]
                if candidates: item['metadata'].append({'field': name, 'block': tag, 'record': candidates[-1]})
        evidence.append(item)
    output = {'bundle_version': '1.1', 'title': (bundle.get('title', 'Investigation')[:120] + ' — replayed evidence'), 'snapshot': bundle['snapshot'], 'evidence': evidence}
    import_bundle(output)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--acquisition', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    payload = package(json.loads(Path(args.bundle).read_text(encoding='utf-8')), json.loads(Path(args.acquisition).read_text(encoding='utf-8')))
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if len(raw.encode()) > 900_000:
        raise ValueError('Packaged evidence exceeds the local import budget')
    with Path(args.output).open('x', encoding='utf-8') as output:
        output.write(raw)
    print('Validated local bundle written; raw evidence is not for Git publication.')


if __name__ == '__main__': main()
