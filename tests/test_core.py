import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
from decimal import Decimal
import ssl
import urllib.error
from app.domain import validate_snapshot, investigate, markdown_report, amount_display
from app.providers import SampleProvider, SnapshotProvider, PolymarketProvider, ProviderUnavailable
from app.storage import CaseStore

class EvidenceTests(unittest.TestCase):
    def setUp(self): self.snapshot = SampleProvider().fetch("routing")

    def test_provider_switch_preserves_evidence_identity(self):
        before = investigate(self.snapshot)
        imported = copy.deepcopy(self.snapshot)
        imported["source"]["provider"] = "Independent local adapter"
        imported["source"]["retrieved_at"] = "2026-09-13T10:00:00Z"
        after = investigate(SnapshotProvider(imported).fetch("ignored"))
        self.assertEqual(before["summary"], after["summary"])
        self.assertEqual([e["id"] for e in before["snapshot"]["events"]], [e["id"] for e in after["snapshot"]["events"]])
        self.assertNotEqual(before["snapshot"]["source"], after["snapshot"]["source"])

    def test_financial_precision_above_javascript_integer_limit(self):
        event = self.snapshot["events"][0]
        event["amount_raw"] = "900719925474099312345678"
        event["asset"]["decimals"] = 18
        self.assertEqual(amount_display(event), "900719.925474099312345678")

    def test_float_amounts_are_rejected(self):
        self.snapshot["events"][0]["amount_raw"] = 1.5
        with self.assertRaises(ValueError): validate_snapshot(self.snapshot)

    def test_same_transaction_different_log_remains_separate(self):
        clone = copy.deepcopy(self.snapshot["events"][0]);clone["locator"] = "log:999"
        self.snapshot["events"].append(clone)
        self.assertEqual(len(validate_snapshot(self.snapshot)["events"]), 7)

    def test_duplicate_is_idempotent_but_conflict_fails(self):
        self.snapshot["events"].append(copy.deepcopy(self.snapshot["events"][0]))
        self.assertEqual(len(validate_snapshot(self.snapshot)["events"]), 6)
        self.snapshot["events"][-1]["amount_raw"] = "1"
        with self.assertRaises(ValueError): validate_snapshot(self.snapshot)

    def test_same_hash_on_different_chains_is_distinct(self):
        clone = copy.deepcopy(self.snapshot["events"][0]);clone["chain"] = "eip155:1"
        self.snapshot["events"].append(clone)
        ids = [e["id"] for e in validate_snapshot(self.snapshot)["events"]]
        self.assertEqual(len(set(ids)), 7)

    def test_unresolved_bridge_does_not_invent_destination(self):
        a = investigate(self.snapshot)
        self.assertEqual(a["summary"]["chain_count"], 1)
        self.assertIn("unresolved", a["boundaries"][0]["detail"])
        bridge = next(e for e in a["snapshot"]["events"] if e["kind"] == "bridge")
        self.assertNotIn("destination_tx", bridge)
        bridge["destination_tx"] = "invented"
        with self.assertRaises(ValueError): validate_snapshot(a["snapshot"])

    def test_unknown_is_not_low_risk(self):
        self.snapshot["events"] = []
        self.assertEqual(investigate(self.snapshot)["summary"]["assessment"], "Not assessed")

    def test_case_reopen_and_append_only_notes(self):
        root=Path(__file__).resolve().parents[1]/".runtime"
        root.mkdir(exist_ok=True)
        path=root/("test-"+uuid.uuid4().hex+".sqlite3")
        try:
            store=CaseStore(path)
            case=store.create("Sample investigation",self.snapshot)
            store.note(case["id"],"Bridge not resolved.","More information needed")
            store.note(case["id"],"A service transfer alone is not suspicious.","Under review")
            reopened=CaseStore(path).get(case["id"])
            self.assertEqual(len(reopened["notes"]),2)
            report=markdown_report(reopened)
            self.assertIn("Synthetic: True",report)
            self.assertIn("Bridge not resolved.",report)
            self.assertIn("No automated finding",report)
            reopened["analysis"]["snapshot"]["source"]["export_allowed"]=False
            with self.assertRaises(PermissionError):markdown_report(reopened)
        finally:
            path.unlink(missing_ok=True)

class PublicAdapterTests(unittest.TestCase):
    wallet="0x"+"a"*40
    def row(self, **changes):
        return {"transaction_hash":"0x"+"1"*64, "proxy_wallet":self.wallet,
                "timestamp":1726000000, "usdc_size":"12.000001", "type":"TRADE", "title":"Fixture activity", **changes}

    def page(self, rows, **pagination):
        return {"data": rows, "pagination": {"has_more": False, "next_cursor": None, **pagination}}

    def test_documented_rows_are_provider_observations(self):
        rows=[self.row()]*2
        def transport(url):
            self.assertIn('/v2/activity?', url)
            self.assertIn('exclude_deposits_withdrawals=false', url)
            return self.page(rows, has_more=True, next_cursor="opaque-cursor")
        s=PolymarketProvider(transport).fetch(self.wallet)
        a=investigate(s)
        self.assertEqual(a["summary"]["event_count"],2)
        self.assertEqual(a["snapshot"]["events"][0]["amount_raw"],"12000001")
        self.assertFalse(s["source"]["export_allowed"])
        self.assertEqual(s["coverage"]["state"],"partial")
        self.assertTrue(s["coverage"]["pagination"]["has_more"])
        self.assertEqual(s["events"][0]["provider_record"]["type"], "TRADE")

    def test_v2_rejects_legacy_or_missing_pagination(self):
        for body in ([self.row()], {"data": []}, self.page([], has_more=True), self.page([], next_cursor="")):
            with self.subTest(body=body), self.assertRaises(ProviderUnavailable):
                PolymarketProvider(lambda url:body).fetch(self.wallet)

    def test_wrong_wallet_or_invalid_metadata_is_rejected(self):
        for change in ({"proxy_wallet":"0x"+"b"*40}, {"transaction_hash":"example"}, {"timestamp": True}, {"timestamp": 1.5}):
            with self.subTest(change=change), self.assertRaises(ProviderUnavailable):
                PolymarketProvider(lambda url:self.page([self.row(**change)])).fetch(self.wallet)

    def test_large_provider_amount_has_no_decimal_context_rounding(self):
        value=Decimal("900719925474099312345678901234.123456")
        snapshot=PolymarketProvider(lambda url:self.page([self.row(usdc_size=value)])).fetch(self.wallet)
        self.assertEqual(snapshot["events"][0]["amount_raw"],"900719925474099312345678901234123456")
        self.assertEqual(snapshot["events"][0]["provider_record"]["usdc_size"],str(value))

    def test_unsupported_provider_amounts_fail_without_rounding(self):
        for value in ("1.0000001", "NaN", "Infinity", "-1", "1e100000", "1e-100000"):
            with self.subTest(value=value), self.assertRaises(ProviderUnavailable):
                PolymarketProvider(lambda url:self.page([self.row(usdc_size=value)])).fetch(self.wallet)

    def test_empty_page_is_not_a_safe_wallet_result(self):
        result=investigate(PolymarketProvider(lambda url:self.page([])).fetch(self.wallet))
        self.assertEqual(result["summary"]["assessment"], "Not assessed")
        self.assertEqual(result["snapshot"]["coverage"]["state"], "partial")

    def test_tls_failure_is_classified_without_fallback(self):
        failure=urllib.error.URLError(ssl.SSLCertVerificationError(1, "test trust failure"))
        with patch("app.providers.urllib.request.urlopen", side_effect=failure) as request:
            with self.assertRaises(ProviderUnavailable) as caught:
                PolymarketProvider().fetch(self.wallet)
        self.assertEqual(caught.exception.code, "tls_verification_failed")
        self.assertEqual(request.call_count,1)

    def test_schema_drift_does_not_become_empty_safe_result(self):
        with self.assertRaises(ProviderUnavailable):PolymarketProvider(lambda url:{"new_schema":[]}).fetch(self.wallet)

    def test_invalid_address_never_contacts_source(self):
        def transport(url):self.fail("Invalid wallet reached network")
        with self.assertRaises(ValueError):PolymarketProvider(transport).fetch("not-a-wallet")

if __name__ == "__main__": unittest.main()
