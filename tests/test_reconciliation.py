import copy
import unittest
from app.reconciliation import reconcile, TRANSFER
from app.collect_case import collect

TX = "0x" + "a" * 64
BH = "0x" + "b" * 64
WALLET = "0x" + "1" * 40
OTHER = "0x" + "2" * 40
CONTRACT = "0x" + "3" * 40


def evidence():
    log = {"transactionHash": TX, "blockHash": BH, "blockNumber": "0x64", "transactionIndex": "0x0",
           "logIndex": "0x7", "removed": False, "address": CONTRACT,
           "topics": [TRANSFER, "0x" + "0" * 24 + WALLET[2:], "0x" + "0" * 24 + OTHER[2:]],
           "data": "0x" + format(900719925474099312345678, "064x")}
    receipt = {"transactionHash": TX, "blockHash": BH, "blockNumber": "0x64", "transactionIndex": "0x0", "status": "0x1", "logs": [log]}
    transaction = {"hash": TX, "blockHash": BH, "blockNumber": "0x64", "transactionIndex": "0x0"}
    block = {"hash": BH, "number": "0x64", "timestamp": "0x65000000", "transactions": [TX]}
    return receipt, transaction, block


class ReconciliationTests(unittest.TestCase):
    def check(self, receipt, transaction, block, **kw):
        return reconcile(TX, WALLET, receipt, transaction, block, kw.get("head", "0x80"), kw.get("chain", "0x89"))

    def test_exact_units_and_independent_log_identity(self):
        result = self.check(*evidence())
        self.assertEqual(result["state"], "rpc_corroborated")
        self.assertEqual(result["transfers"][0]["amount_raw"], "900719925474099312345678")
        self.assertEqual(result["transfers"][0]["locator"], "log:7")
        self.assertIsNone(result["transfers"][0]["decimals"])
        self.assertEqual(result["confirmations_at_retrieval"], 29)

    def test_wrong_chain_fails(self):
        with self.assertRaises(ValueError): self.check(*evidence(), chain="0x1")

    def test_pending_is_unresolved_not_inactive(self):
        self.assertEqual(self.check(None, None, None)["state"], "unresolved")

    def test_reorganization_or_wrong_receipt_is_rejected(self):
        for target, field, value in [(0, "transactionHash", BH), (1, "blockHash", TX), (2, "hash", TX), (2, "transactions", [BH]), (1, "transactionIndex", "0x1")]:
            items = list(evidence()); items[target][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.check(*items)

    def test_removed_duplicate_and_foreign_logs_rejected(self):
        for field, value in [("removed", True), ("transactionHash", BH), ("blockNumber", "0x65"), ("transactionIndex", "0x1")]:
            items = list(evidence()); items[0]["logs"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.check(*items)
        items = list(evidence()); items[0]["logs"] *= 2
        with self.assertRaises(ValueError): self.check(*items)

    def test_erc721_and_unrelated_logs_do_not_become_subject_transfers(self):
        items = list(evidence()); items[0]["logs"][0]["topics"].append("0x" + "0" * 64)
        self.assertFalse(self.check(*items)["subject_in_transfer_logs"])
        items = list(evidence()); items[0]["logs"][0]["topics"][1] = "0x" + "0" * 24 + OTHER[2:]
        self.assertFalse(self.check(*items)["subject_in_transfer_logs"])

    def test_future_head_and_malformed_units_rejected(self):
        with self.assertRaises(ValueError): self.check(*evidence(), head="0x63")
        items = list(evidence()); items[0]["logs"][0]["data"] = "0x1"
        with self.assertRaises(ValueError): self.check(*items)

    def test_reverted_transaction_is_not_success(self):
        items = list(evidence()); items[0].update(status="0x0", logs=[])
        self.assertEqual(self.check(*items)["execution"], "reverted")

    def test_collection_preserves_provider_assertions_and_export_policy(self):
        class FakeAcquisition:
            def get(self, url):
                if "/v2/trades" in url:
                    return {"data": [{"proxy_wallet": WALLET, "transaction_hash": TX}]}
                return {"data": [{"proxy_wallet": WALLET, "transaction_hash": TX, "timestamp": 1694498816,
                                  "usdc_size": "1", "type": "TRADE"}], "pagination": {"has_more": False, "next_cursor": None}}

            def rpc(self, method, params):
                receipt, transaction, block = evidence()
                return {"eth_chainId": "0x89", "eth_getTransactionReceipt": receipt,
                        "eth_getTransactionByHash": transaction, "eth_getBlockByNumber": block,
                        "eth_blockNumber": "0x80"}[method]
        bundle = collect(FakeAcquisition())
        self.assertEqual(bundle["chain_checks"][0]["state"], "rpc_corroborated")
        self.assertEqual(bundle["snapshot"]["events"][0]["status"], "provider_reported")
        self.assertFalse(bundle["snapshot"]["source"]["export_allowed"])
        self.assertFalse(bundle["policy"]["public_redistribution"])


if __name__ == "__main__": unittest.main()
