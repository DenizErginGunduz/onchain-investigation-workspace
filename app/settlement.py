"""Narrow Polymarket v2 complementary-buy interpretation, not a universal decoder.

ABI references: Polymarket/ctf-exchange-v2 Events.sol, Structs.sol; ERC-1155.
Call only after receipt identity/log validation. Contract addresses are documented
Polygon deployments; the interpretation is not a deployed-bytecode audit.
"""
import re
from .reconciliation import TRANSFER, quantity
from .review_case import abi_address

COLLATERAL = '0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb'
EXCHANGE = '0xe2222d279d744050d28e00520010520000310f59'
CTF = '0x4d97dcd97ec945f40cf65f87097ace5ea0476045'
ORDER_FILLED = '0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee'
ORDERS_MATCHED = '0x174b3811690657c217184f89418266767c87e4805d09680c39fc9c031c0cab7c'
FEE_CHARGED = '0x55bb3cade9d43b798a4fe5ffdd05024b2d7870df53920673bfc7e68047cd0ab1'
TRANSFER_SINGLE = '0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62'


def words(data, count):
    if not isinstance(data, str) or not re.fullmatch(r'0x[0-9a-fA-F]{' + str(count * 64) + '}', data):
        raise ValueError('Unexpected event data length/encoding')
    return [int(data[2 + i * 64:2 + (i + 1) * 64], 16) for i in range(count)]


def decode(receipt):
    events = []
    shapes = {(COLLATERAL, TRANSFER): (3, 1, 'transfer'),
              (CTF, TRANSFER_SINGLE): (4, 2, 'outcome_transfer'),
              (EXCHANGE, ORDER_FILLED): (4, 7, 'fill'),
              (EXCHANGE, ORDERS_MATCHED): (3, 4, 'match'),
              (EXCHANGE, FEE_CHARGED): (2, 1, 'fee')}
    for log in receipt['logs']:
        topics = log['topics']
        key = (log['address'].lower(), topics[0].lower() if topics else '')
        if key not in shapes:
            continue
        ntopics, nwords, kind = shapes[key]
        if len(topics) != ntopics or not all(isinstance(t, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', t) for t in topics):
            raise ValueError('Malformed supported event topics')
        values = words(log['data'], nwords)
        event = {'kind': kind, 'locator': 'log:' + str(quantity(log['logIndex']))}
        if kind == 'transfer':
            event.update(sender=abi_address(topics[1]), recipient=abi_address(topics[2]), amount_raw=str(values[0]))
        elif kind == 'outcome_transfer':
            event.update(operator=abi_address(topics[1]), sender=abi_address(topics[2]), recipient=abi_address(topics[3]), token_id=str(values[0]), amount_raw=str(values[1]))
        elif kind in ('fill', 'match'):
            if values[0] not in (0, 1):
                raise ValueError('Unknown order side')
            event.update(order_hash=topics[1].lower(), maker=abi_address(topics[2]), side='BUY' if values[0] == 0 else 'SELL',
                         token_id=str(values[1]), maker_amount_raw=str(values[2]), taker_amount_raw=str(values[3]))
            if kind == 'fill':
                event.update(taker=abi_address(topics[3]), fee_raw=str(values[4]), builder='0x' + format(values[5], '064x'), metadata='0x' + format(values[6], '064x'))
        else:
            event.update(recipient=abi_address(topics[1]), amount_raw=str(values[0]))
        events.append(event)
    return events


def simple_buy(receipt, subject):
    events = decode(receipt)
    result = {'state': 'unresolved', 'events': events,
              'basis': 'Documented Polygon contracts and v2 ABI; requires separately corroborated receipt. Narrow one-buyer/one-seller pattern only.'}
    buys = [e for e in events if e['kind'] == 'fill' and e['maker'] == subject.lower() and e['side'] == 'BUY' and e['taker'] == EXCHANGE]
    sells = [e for e in events if e['kind'] == 'fill' and e['side'] == 'SELL' and e['taker'] == subject.lower()]
    fees = [e for e in events if e['kind'] == 'fee']
    if len(buys) != 1 or len(sells) != 1 or len(fees) != 1:
        result['reason'] = 'Outside unique complementary-buy scope'
        return result
    buy, sell, fee = buys[0], sells[0], fees[0]
    if buy['token_id'] != sell['token_id'] or buy['maker_amount_raw'] != sell['taker_amount_raw'] or buy['taker_amount_raw'] != sell['maker_amount_raw'] or fee['amount_raw'] != buy['fee_raw'] or sell['fee_raw'] != '0':
        result['reason'] = 'Order quantities or fee do not reconcile'
        return result
    matches = [e for e in events if e['kind'] == 'match' and all(e[k] == buy[k] for k in ('order_hash', 'maker', 'side', 'token_id', 'maker_amount_raw', 'taker_amount_raw'))]
    transfers = [e for e in events if e['kind'] == 'transfer' and subject.lower() in (e['sender'], e['recipient'])]
    payments = [e for e in transfers if e['sender'] == subject.lower() and e['recipient'] == sell['maker'] and e['amount_raw'] == buy['maker_amount_raw']]
    fee_transfers = [e for e in transfers if e['sender'] == subject.lower() and e['recipient'] == fee['recipient'] and e['amount_raw'] == buy['fee_raw']]
    outcomes = [e for e in events if e['kind'] == 'outcome_transfer' and subject.lower() in (e['sender'], e['recipient'])]
    delivered = [e for e in outcomes if e['sender'] == sell['maker'] and e['recipient'] == subject.lower() and e['operator'] == EXCHANGE and e['token_id'] == buy['token_id'] and e['amount_raw'] == buy['taker_amount_raw']]
    if not (len(matches) == len(payments) == len(fee_transfers) == len(delivered) == 1 and len(transfers) == 2 and len(outcomes) == 1 and payments[0]['locator'] != fee_transfers[0]['locator']):
        result['reason'] = 'Transfer delivery/payment/match evidence missing or ambiguous'
        return result
    result.update(state='complementary_buy_supported', collateral_contract=COLLATERAL, exchange_contract=EXCHANGE,
                  seller=sell['maker'], fee_receiver=fee['recipient'], collateral_payment_raw=buy['maker_amount_raw'],
                  collateral_fee_raw=buy['fee_raw'], collateral_total_debit_raw=str(int(buy['maker_amount_raw']) + int(buy['fee_raw'])),
                  outcome_token_id=buy['token_id'], outcome_received_raw=buy['taker_amount_raw'],
                  evidence_locators=[e['locator'] for e in (buy, sell, matches[0], fee, payments[0], fee_transfers[0], delivered[0])])
    return result
