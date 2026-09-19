"""Providers end at the snapshot contract; UI and case storage never call vendors."""
from datetime import datetime, timezone
from hashlib import sha256
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Protocol
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

class ProviderUnavailable(Exception):
    def __init__(self, message, code="provider_unavailable"):
        super().__init__(message)
        self.code = code

class InvestigationProvider(Protocol):
    def fetch(self, subject: str) -> dict: ...

class SampleProvider:
    def fetch(self, subject):
        if subject not in {"routing", "ordinary"}:
            raise ValueError("Unknown demonstration case.")
        return json.loads((ROOT / "fixtures" / f"{subject}.json").read_text(encoding="utf-8"))

class SnapshotProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def fetch(self, subject):
        return self.snapshot

class PolymarketProvider:
    """One bounded public-activity page. No chain-history or sanctions claim."""
    def __init__(self, transport=None):
        self.transport = transport or self._get

    def _get(self, url):
        request = urllib.request.Request(url, headers={"User-Agent": "OnchainInvestigation/0.1", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise ProviderUnavailable("The provider response exceeded the local size limit.")
                return json.loads(body, parse_float=Decimal)
        except ProviderUnavailable:
            raise
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise ProviderUnavailable("The public source certificate could not be verified. Check the runtime's trusted certificate configuration. No data was saved.", "tls_verification_failed") from exc
            raise ProviderUnavailable("The public source could not be reached. No data was saved.", "network_unavailable") from exc
        except Exception as exc:
            # Do not disable TLS verification or silently substitute example data.
            raise ProviderUnavailable("Public data could not be retrieved securely. No result or risk conclusion was produced.") from exc

    def fetch(self, subject):
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", subject):
            raise ValueError("Enter a valid EVM wallet address.")
        query = urllib.parse.urlencode({"user": subject, "limit": 50, "sort_direction": "DESC", "exclude_deposits_withdrawals": "false"})
        # Explicit v2 schema only. Never silently fall back to legacy data.
        url = "https://data-api.polymarket.com/v2/activity?" + query
        body = self.transport(url)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list) or not isinstance(body.get("pagination"), dict):
            raise ProviderUnavailable("The public source schema differs from the documented activity contract. Import stopped; nothing was inferred.")
        pagination = body["pagination"]
        if type(pagination.get("has_more")) is not bool or "next_cursor" not in pagination:
            raise ProviderUnavailable("The public source pagination metadata is incomplete.")
        cursor = pagination["next_cursor"]
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ProviderUnavailable("The public source cursor is invalid.")
        if pagination["has_more"] != (cursor is not None):
            raise ProviderUnavailable("The public source pagination metadata conflicts.")
        rows = body["data"]
        if len(rows) > 50:
            raise ProviderUnavailable("The source did not respect the requested page limit.")
        events = []
        occurrence = {}
        for row in rows:
            try:
                # Strict mapping: an incompatible schema must not be silently coerced.
                tx = row["transaction_hash"]
                timestamp = row["timestamp"]
                value = Decimal(str(row["usdc_size"]))
                with localcontext() as context:
                    context.prec = 100
                    if not value.is_finite() or value < 0 or value >= Decimal("1e72") or value.as_tuple().exponent < -36:
                        raise ValueError("Unsupported amount range")
                    scaled = value * 10**6
                    if scaled != scaled.to_integral_value():
                        raise ValueError("Unsupported amount precision")
                    amount_raw = str(int(scaled))
                if not isinstance(tx, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx) or type(timestamp) is not int:
                    raise ValueError("Invalid activity metadata")
                if not isinstance(row.get("proxy_wallet"), str) or row["proxy_wallet"].lower() != subject.lower():
                    raise ValueError("Activity belongs to a different or unspecified wallet")
                record = json.loads(json.dumps(row, sort_keys=True, default=str))
                row_digest = sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:20]
                occurrence[row_digest] = occurrence.get(row_digest, 0) + 1
                # Preserve repeated rows within this page. This is a provider
                # observation locator, never a claimed on-chain log index.
                locator = f"activity-row:{row_digest}:{occurrence[row_digest]}"
                events.append({"chain": "eip155:137", "tx": tx, "locator": locator,
                    "timestamp": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                    "from": subject.lower(), "to": "Polymarket activity", "label": str(row.get("title") or row.get("type") or "Reported activity")[:200],
                    "kind": "market_activity", "status": "provider_reported", "amount_raw": amount_raw,
                    "provider_record": record,
                    "asset": {"id": "provider:polymarket:v2:usdc_size", "symbol": "reported USD", "decimals": 6}})
            except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError) as exc:
                raise ProviderUnavailable("Activity fields require reconciliation before this source can be used. No partial interpretation was saved.") from exc
        return {"schema_version": "1.0", "synthetic": False, "subject": {"chain": "eip155:137", "address": subject.lower()},
            "source": {"provider": "Polymarket public activity v2", "retrieved_at": datetime.now(timezone.utc).isoformat(), "reference": url, "export_allowed": False},
            "coverage": {"state": "partial", "pagination": pagination, "limitations": ["One public v2 activity page, maximum 50 observations; not complete wallet history. Further pages are not fetched automatically.",
                "Deposits and withdrawals are requested, but this is still the source's activity feed, not all wallet transfers. TIP activity is not requested.",
                "Reported USD amounts retain at most six decimal places; unsupported precision is rejected. This is not a verified token balance or profit calculation.",
                "Provider-reported economic activity; not independently verified token transfers or chain receipts.",
                "Graph edges indicate subject-to-activity associations, not inferred transfer direction. Row locators are snapshot observations, not chain event IDs.",
                "No service attribution, sanctions screening, bridge correspondence or market-abuse detection.",
                "Redistribution rights have not been established; export disabled."]}, "events": events}

PROVIDERS = [
    {"id": "sample", "name": "Example cases", "state": "available", "cost": "$0", "description": "Clearly labeled synthetic cases. No API key."},
    {"id": "snapshot", "name": "Portable snapshot", "state": "available", "cost": "$0", "description": "Import the 1.0 evidence contract from your own or permitted external adapter."},
    {"id": "polymarket", "name": "Polymarket public", "state": "experimental", "cost": "$0", "description": "Bounded activity lookup. Live schema and receipt reconciliation still required."},
    {"id": "licensed", "name": "Licensed intelligence", "state": "not_configured", "cost": "Not selected", "description": "No subscription, proprietary labels or vendor connection configured."}]

def fetch_snapshot(provider, subject, payload=None):
    if provider == "sample":
        return SampleProvider().fetch(subject)
    if provider == "snapshot":
        return SnapshotProvider(payload).fetch(subject)
    if provider == "polymarket":
        return PolymarketProvider().fetch(subject)
    raise ValueError("This provider is not configured.")
