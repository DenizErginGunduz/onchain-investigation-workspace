"""Manually run a small public-data validation case. No private keys or writes to a chain."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from .domain import validate_snapshot
from .providers import PolymarketProvider
from .reconciliation import ADDRESS, HASH, reconcile

RPC_URL = "https://polygon-bor-rpc.publicnode.com"
DISCOVERY_URL = "https://data-api.polymarket.com/v2/trades?limit=10"


class Acquisition:
    def __init__(self):
        self.records = []

    def get(self, url, method=None, params=None):
        payload = None if method is None else {"jsonrpc": "2.0", "id": len(self.records) + 1, "method": method, "params": params}
        request = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(),
                                         headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "OnchainInvestigation/0.1"})
        record = {"url": url, "request": payload, "retrieved_at": datetime.now(timezone.utc).isoformat(), "tls_verification": True}
        self.records.append(record)
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read(1_000_001)
                record["http_status"] = response.status
            if len(raw) > 1_000_000:
                raise ValueError("Response exceeds 1 MB")
            record["sha256"] = sha256(raw).hexdigest()
            record["body_utf8"] = raw.decode("utf-8")
            body = json.loads(raw, parse_float=Decimal)
            if payload is not None:
                if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") != payload["id"] or "error" in body or "result" not in body:
                    raise ValueError("RPC error or invalid response envelope")
                return body["result"]
            return body
        except urllib.error.HTTPError as exc:
            record["http_status"] = exc.code
            raise ValueError("Upstream HTTP " + str(exc.code)) from exc
        except urllib.error.URLError as exc:
            record["failure"] = type(exc.reason).__name__
            raise ValueError("Secure source connection unavailable") from exc

    def rpc(self, method, params):
        return self.get(RPC_URL, method, params)


def collect(acquisition, wallet=None):
    selection = {"rule": "Explicitly supplied public address; no ownership or suspicion claim."}
    if wallet is None:
        discovery = acquisition.get(DISCOVERY_URL)
        if not isinstance(discovery, dict) or not isinstance(discovery.get("data"), list) or len(discovery["data"]) > 10:
            raise ValueError("Discovery schema or limit mismatch")
        candidates = [r for r in discovery["data"] if isinstance(r, dict) and ADDRESS.fullmatch(r.get("proxy_wallet", "")) and HASH.fullmatch(r.get("transaction_hash", ""))]
        if not candidates:
            raise ValueError("No valid public subject in the bounded discovery page")
        wallet = candidates[0]["proxy_wallet"].lower()
        selection = {"rule": "First structurally valid row of one ten-row public trades page; convenience sample, not a suspicious-activity selection.", "source": DISCOVERY_URL, "transaction": candidates[0]["transaction_hash"]}
    snapshot = validate_snapshot(PolymarketProvider(acquisition.get).fetch(wallet))
    if not snapshot["events"]:
        raise ValueError("Selected source returned no activity; no substitute case was created")
    transactions = list(dict.fromkeys(e["tx"].lower() for e in snapshot["events"]))[:3]
    checks = []
    chain_id = acquisition.rpc("eth_chainId", [])
    if chain_id != "0x89":
        raise ValueError("RPC is not Polygon PoS")
    for tx_hash in transactions:
        try:
            receipt = acquisition.rpc("eth_getTransactionReceipt", [tx_hash])
            transaction = acquisition.rpc("eth_getTransactionByHash", [tx_hash])
            block = acquisition.rpc("eth_getBlockByNumber", [receipt["blockNumber"], False]) if receipt else None
            head = acquisition.rpc("eth_blockNumber", [])
            check = reconcile(tx_hash, wallet, receipt, transaction, block, head, chain_id)
            check["rpc_source"] = RPC_URL
            check["activity_timestamps"] = sorted({e["timestamp"] for e in snapshot["events"] if e["tx"].lower() == tx_hash})
            checks.append(check)
        except (ValueError, TypeError, KeyError) as exc:
            checks.append({"tx": tx_hash, "state": "unresolved", "reason": str(exc), "transfers": []})
    return {"bundle_version": "1.0", "title": "Public activity and Polygon receipt spot check", "selection": selection,
            "snapshot": snapshot, "chain_checks": checks,
            "policy": {"purpose": "Bounded technical validation", "public_redistribution": False,
                       "license_status": "No commercial redistribution license established; source export flag remains false."},
            "coverage": "One activity page, maximum 50 observations; first three unique transaction hashes only. No full wallet history or cross-chain tracing.",
            "finding": "No AML, fraud, insider activity or wallet ownership finding. Provider activity assertions remain distinct from RPC-correlated receipts."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=None)
    parser.add_argument("--output", default=".runtime/public-case")
    args = parser.parse_args()
    if args.wallet is not None and not ADDRESS.fullmatch(args.wallet):
        parser.error("Invalid public EVM address")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    acquisition = Acquisition()
    bundle = None
    outcome = {"state": "failed", "code_revision": os.environ.get("GITHUB_SHA", "local-unrecorded"),
               "run_id": os.environ.get("GITHUB_RUN_ID"), "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        bundle = collect(acquisition, args.wallet)
        (output / "case-bundle.json").write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
        (output / "snapshot.json").write_text(json.dumps(bundle["snapshot"], indent=2, default=str), encoding="utf-8")
        outcome.update(state="collected", observations=len(bundle["snapshot"]["events"]), checked_transactions=len(bundle["chain_checks"]),
                       corroborated=sum(c["state"] == "rpc_corroborated" for c in bundle["chain_checks"]))
    except Exception as exc:
        outcome["error_type"] = type(exc).__name__
        outcome["message"] = str(exc)
    (output / "acquisition.json").write_text(json.dumps(acquisition.records, indent=2, default=str), encoding="utf-8")
    (output / "outcome.json").write_text(json.dumps(outcome, indent=2), encoding="utf-8")
    manifest = {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(outcome, indent=2))
    return 0 if bundle and outcome.get("corroborated", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
