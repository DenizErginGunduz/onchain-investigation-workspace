"""Read-only follow-up of a bounded saved case; preserves the first acquisition."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from .collect_case import Acquisition, RPC_URL
from .reconciliation import ADDRESS, HASH, quantity, reconcile


def abi_uint(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError("Expected one ABI word")
    return int(value, 16)


def abi_address(value):
    abi_uint(value)
    if value[2:26] != "0" * 24:
        raise ValueError("Invalid ABI address padding")
    return "0x" + value[-40:].lower()


def abi_string(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", value):
        raise ValueError("Invalid ABI string encoding")
    raw = bytes.fromhex(value[2:])
    if len(raw) < 64 or int.from_bytes(raw[:32], 'big') != 32:
        raise ValueError("Expected dynamic ABI string")
    length = int.from_bytes(raw[32:64], 'big')
    if length > 256 or len(raw) != 64 + ((length + 31) // 32) * 32 or any(raw[64 + length:]):
        raise ValueError("Invalid ABI string length or padding")
    return raw[64:64 + length].decode('utf-8')


def code_evidence(code):
    if code is None:
        return {'code_present': None, 'code_state': 'unavailable', 'code_sha256': None}
    if not isinstance(code, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', code):
        raise ValueError('Malformed contract code')
    raw = bytes.fromhex(code[2:])
    return {'code_present': bool(raw), 'code_state': 'present' if raw else 'empty',
            'code_sha256': sha256(raw).hexdigest() if raw else None}


def finality_check(original, current, finalized, finalized_canonical, head):
    """RPC-reported finality, not a locally verified consensus/ancestry proof."""
    if current.get('state') != 'rpc_corroborated':
        return {'state': 'unresolved', 'reason': 'Receipt not corroborated'}
    if (current['block_hash'], current['block_number']) != (original['block_hash'], original['block_number']):
        return {'state': 'changed_block', 'reason': 'Original block identity changed; investigate reorganization or source conflict'}
    if not isinstance(finalized, dict) or not isinstance(finalized_canonical, dict):
        return {'state': 'unresolved', 'reason': 'Finalized tag evidence unavailable'}
    number = quantity(finalized.get('number'))
    block_hash = finalized.get('hash')
    if not isinstance(block_hash, str) or not HASH.fullmatch(block_hash):
        raise ValueError('Invalid finalized block hash')
    if (finalized_canonical.get('number'), finalized_canonical.get('hash')) != (finalized['number'], block_hash):
        raise ValueError('Finalized tag differs from canonical block')
    if number > quantity(head):
        raise ValueError('Finalized block ahead of observed head')
    return {'state': 'rpc_reported_finalized' if number >= current['block_number'] else 'not_yet_finalized',
            'original_block_unchanged': True, 'finalized_block_number': number,
            'finalized_block_hash': block_hash.lower(),
            'basis': 'Same RPC: canonical receipt block unchanged and at/below canonical finalized tag; no local consensus or ancestry proof'}


def review(acquisition, bundle):
    wallet = bundle['snapshot']['subject']['address']
    originals = bundle['chain_checks']
    if not ADDRESS.fullmatch(wallet) or not 1 <= len(originals) <= 3:
        raise ValueError('Expected one public subject and at most three prior checks')
    if len({c['tx'] for c in originals}) != len(originals):
        raise ValueError('Duplicate original transaction')
    chain = acquisition.rpc('eth_chainId', [])
    if quantity(chain) != 137:
        raise ValueError('Wrong chain')
    failures = []

    def optional(method, params):
        try:
            return acquisition.rpc(method, params)
        except (ValueError, TypeError, KeyError) as exc:
            failures.append({'method': method, 'params': params, 'reason': str(exc)})
            return None

    finalized = optional('eth_getBlockByNumber', ['finalized', False])
    finalized_canonical = optional('eth_getBlockByNumber', [finalized['number'], False]) if finalized else None
    results = []
    metadata = {}
    signatures = {}
    for signature in ('getCollateral()', 'getCtf()', 'getFeeReceiver()'):
        digest = optional('web3_sha3', ['0x' + signature.encode().hex()])
        if isinstance(digest, str) and HASH.fullmatch(digest):
            signatures[signature] = digest[:10]
    for original in originals:
        tx_hash = original['tx']
        if not HASH.fullmatch(tx_hash):
            raise ValueError('Invalid saved transaction')
        receipt = acquisition.rpc('eth_getTransactionReceipt', [tx_hash])
        transaction = acquisition.rpc('eth_getTransactionByHash', [tx_hash])
        block = acquisition.rpc('eth_getBlockByNumber', [receipt['blockNumber'], False]) if receipt else None
        head = acquisition.rpc('eth_blockNumber', [])
        current = reconcile(tx_hash, wallet, receipt, transaction, block, head, chain)
        result = {'tx': tx_hash, 'original': original, 'current': current,
                  'finality': finality_check(original, current, finalized, finalized_canonical, head)}
        results.append(result)
        if current['state'] != 'rpc_corroborated':
            continue
        exchange = transaction['to'].lower()
        contracts = sorted({t['contract'] for t in current['transfers']})
        if len(contracts) > 5 or not ADDRESS.fullmatch(exchange):
            raise ValueError('Metadata scope exceeds bounded review')
        for tag in dict.fromkeys([receipt['blockNumber'], finalized['number'] if finalized else receipt['blockNumber']]):
            for contract in contracts + [exchange]:
                key = contract + '@' + tag
                if key in metadata:
                    continue
                entry = {'contract': contract, 'block_tag': tag, 'values': {}, 'failures': {}}
                metadata[key] = entry
                code = optional('eth_getCode', [contract, tag])
                entry.update(code_evidence(code))
                methods = [('decimals', '0x313ce567', abi_uint), ('name', '0x06fdde03', abi_string), ('symbol', '0x95d89b41', abi_string)] if contract in contracts else [(sig, selector, abi_address) for sig, selector in signatures.items()]
                for name, selector, decoder in methods:
                    raw = optional('eth_call', [{'to': contract, 'data': selector}, tag])
                    try:
                        decoded = decoder(raw)
                        if name == 'decimals' and decoded > 255:
                            raise ValueError('Decimals outside uint8')
                        entry['values'][name] = decoded
                    except (ValueError, TypeError) as exc:
                        entry['failures'][name] = str(exc)
    return {'review_version': '1.0', 'rpc_source': RPC_URL, 'subject': wallet,
            'checks': results, 'metadata': list(metadata.values()), 'optional_failures': failures,
            'limits': ['One RPC source; no independent consensus proof.', 'Contract metadata is block-specific RPC output, not a source-code or backing audit.', 'Original provider assertions and export policy remain unchanged.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    original_bytes = Path(args.bundle).read_bytes()
    bundle = json.loads(original_bytes)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    acquisition = Acquisition()
    outcome = {'state': 'failed', 'created_at': datetime.now(timezone.utc).isoformat(),
               'input_bundle_sha256': sha256(original_bytes).hexdigest()}
    try:
        result = review(acquisition, bundle)
        (output / 'review.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        outcome.update(state='reviewed', transactions=len(result['checks']),
                       rpc_reported_finalized=sum(c['finality']['state'] == 'rpc_reported_finalized' for c in result['checks']))
    except Exception as exc:
        outcome.update(error_type=type(exc).__name__, message=str(exc))
    outcome['requests'] = len(acquisition.records)
    for name, data in [('acquisition.json', acquisition.records), ('outcome.json', outcome)]:
        (output / name).write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
    (output / 'manifest.json').write_text(json.dumps({p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir())}, indent=2), encoding='utf-8')
    print(json.dumps(outcome, indent=2))
    return 0 if outcome['state'] == 'reviewed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
