import copy
import unittest
from app.review_case import abi_uint, abi_string, abi_address, code_evidence, finality_check
from app.settlement import (COLLATERAL, EXCHANGE, CTF, TRANSFER, TRANSFER_SINGLE,
                            ORDER_FILLED, ORDERS_MATCHED, FEE_CHARGED, simple_buy)

BUYER = '0x' + '1' * 40
SELLER = '0x' + '2' * 40
FEE = '0x' + '3' * 40
ORDER = '0x' + '4' * 64
BLOCK = '0x' + '5' * 64


def word(n):
    return '0x' + format(n, '064x')


def address(a):
    return '0x' + '0' * 24 + a[2:]


def fixture():
    def log(i, contract, topics, values):
        return {'logIndex': hex(i), 'address': contract, 'topics': topics,
                'data': '0x' + ''.join(format(v, '064x') for v in values)}
    return {'logs': [
        log(1, CTF, [TRANSFER_SINGLE, address(EXCHANGE), address(SELLER), address(BUYER)], [99, 5000000]),
        log(2, COLLATERAL, [TRANSFER, address(BUYER), address(SELLER)], [10000]),
        log(3, EXCHANGE, [ORDER_FILLED, word(7), address(SELLER), address(BUYER)], [1, 99, 5000000, 10000, 0, 0, 0]),
        log(4, EXCHANGE, [FEE_CHARGED, address(FEE)], [290]),
        log(5, EXCHANGE, [ORDER_FILLED, ORDER, address(BUYER), address(EXCHANGE)], [0, 99, 10000, 5000000, 290, 0, 0]),
        log(6, EXCHANGE, [ORDERS_MATCHED, ORDER, address(BUYER)], [0, 99, 10000, 5000000]),
        log(7, COLLATERAL, [TRANSFER, address(BUYER), address(FEE)], [290])]}


class ReviewTests(unittest.TestCase):
    def test_finality_requires_unchanged_canonical_original(self):
        original = {'block_hash': BLOCK, 'block_number': 100}
        current = dict(original, state='rpc_corroborated')
        finalized = {'number': '0x64', 'hash': BLOCK}
        self.assertEqual(finality_check(original, current, finalized, finalized, '0x65')['state'], 'rpc_reported_finalized')
        current['block_hash'] = ORDER
        self.assertEqual(finality_check(original, current, finalized, finalized, '0x65')['state'], 'changed_block')

    def test_missing_or_lagging_finalized_tag_is_not_finality(self):
        original = {'block_hash': BLOCK, 'block_number': 100}
        current = dict(original, state='rpc_corroborated')
        self.assertEqual(finality_check(original, current, None, None, '0x1000')['state'], 'unresolved')
        behind = {'number': '0x63', 'hash': ORDER}
        self.assertEqual(finality_check(original, current, behind, behind, '0x1000')['state'], 'not_yet_finalized')

    def test_inconsistent_finalized_tag_is_rejected(self):
        original = {'block_hash': BLOCK, 'block_number': 100}
        current = dict(original, state='rpc_corroborated')
        finalized = {'number': '0x64', 'hash': BLOCK}
        with self.assertRaises(ValueError): finality_check(original, current, finalized, dict(finalized, hash=ORDER), '0x65')
        with self.assertRaises(ValueError): finality_check(original, current, finalized, finalized, '0x63')

    def test_missing_code_is_not_empty_code(self):
        self.assertIsNone(code_evidence(None)['code_present'])
        self.assertEqual(code_evidence(None)['code_state'], 'unavailable')
        self.assertFalse(code_evidence('0x')['code_present'])
        self.assertTrue(code_evidence('0x00')['code_present'])
        with self.assertRaises(ValueError): code_evidence('0x1')

    def test_abi_decoding_rejects_malformed_and_preserves_integers(self):
        self.assertEqual(abi_uint(word(2**200 + 7)), 2**200 + 7)
        self.assertEqual(abi_address(address(BUYER)), BUYER)
        encoded = word(32) + word(4)[2:] + b'pUSD'.hex().ljust(64, '0')
        self.assertEqual(abi_string(encoded), 'pUSD')
        for bad in ('0x', word(0) + encoded[66:], encoded[:-2], encoded + '00'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): abi_string(bad)
        with self.assertRaises(ValueError): abi_address(word(2**255))

    def test_complementary_buy_matches_all_legs_without_double_counting(self):
        result = simple_buy(fixture(), BUYER)
        self.assertEqual(result['state'], 'complementary_buy_supported')
        self.assertEqual(result['collateral_total_debit_raw'], '10290')
        self.assertEqual(result['outcome_received_raw'], '5000000')
        self.assertEqual(len(result['evidence_locators']), 7)

    def test_fee_or_delivery_mismatch_does_not_support_trade(self):
        for index in (0, 1, 3, 6):
            data = fixture()
            data['logs'][index]['data'] = data['logs'][index]['data'][:-1] + 'f'
            with self.subTest(index=index): self.assertEqual(simple_buy(data, BUYER)['state'], 'unresolved')

    def test_wrong_contract_and_missing_order_match_are_not_supported(self):
        data = fixture(); data['logs'][5]['address'] = SELLER
        self.assertEqual(simple_buy(data, BUYER)['state'], 'unresolved')
        data = fixture(); data['logs'][0]['topics'][3] = address(SELLER)
        self.assertEqual(simple_buy(data, BUYER)['state'], 'unresolved')

    def test_ambiguous_extra_fee_does_not_get_allocated(self):
        data = fixture(); extra = copy.deepcopy(data['logs'][3]); extra['logIndex'] = '0x8'; data['logs'].append(extra)
        self.assertEqual(simple_buy(data, BUYER)['state'], 'unresolved')

    def test_malformed_supported_event_fails_closed(self):
        data = fixture(); data['logs'][4]['data'] = '0x00'
        with self.assertRaises(ValueError): simple_buy(data, BUYER)
        data = fixture(); data['logs'][4]['topics'][2] = word(2**255)
        with self.assertRaises(ValueError): simple_buy(data, BUYER)
