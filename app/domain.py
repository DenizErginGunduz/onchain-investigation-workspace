"""Provider-neutral evidence contract. No provider score becomes a crime verdict."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

SCHEMA_VERSION = "1.0"
KINDS = {"transfer", "bridge", "market_activity", "service_boundary"}
STATES = {"observed", "provider_reported", "unresolved", "synthetic"}

def stable_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def evidence_id(event):
    """Identity is chain/event based; provider IDs and retrieval time are not keys."""
    identity = {k: event.get(k) for k in ("chain", "tx", "locator", "kind")}
    return "ev_" + sha256(stable_json(identity).encode()).hexdigest()[:24]

def validate_snapshot(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported snapshot schema. Expected 1.0.")
    data = deepcopy(payload)
    for field in ("subject", "source", "coverage"):
        if not isinstance(data.get(field), dict):
            raise ValueError(f"Missing {field} object.")
    for field in ("chain", "address"):
        if not isinstance(data["subject"].get(field), str) or not data["subject"][field]:
            raise ValueError(f"Missing subject {field}.")
    source = data["source"]
    for field in ("provider", "retrieved_at", "reference"):
        if not isinstance(source.get(field), str) or not source[field]:
            raise ValueError(f"Missing source {field}.")
    if type(data.get("synthetic")) is not bool:
        raise ValueError("Declare whether the snapshot is synthetic.")
    if type(source.get("export_allowed")) is not bool:
        raise ValueError("Declare the source export policy.")
    events = data.get("events")
    if not isinstance(events, list) or len(events) > 200:
        raise ValueError("A snapshot must contain at most 200 events.")
    if data["coverage"].get("state") not in {"bounded", "partial", "unavailable"}:
        raise ValueError("Declare bounded, partial or unavailable coverage.")
    if not isinstance(data["coverage"].get("limitations"), list):
        raise ValueError("Coverage limitations are required.")
    seen = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Events must be objects.")
        for key in ("chain", "tx", "locator", "timestamp", "from", "to", "label"):
            if not isinstance(event.get(key), str) or not event[key]:
                raise ValueError(f"Missing event {key}.")
        if event.get("kind") not in KINDS or event.get("status") not in STATES:
            raise ValueError("Unsupported event kind or evidence status.")
        asset = event.get("asset", {})
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
            raise ValueError("A chain-scoped asset identifier is required.")
        decimals = asset.get("decimals")
        if type(decimals) is not int or not 0 <= decimals <= 36:
            raise ValueError("Invalid asset decimals.")
        amount = event.get("amount_raw")
        if not isinstance(amount, str) or not re.fullmatch(r"\d{1,78}", amount):
            raise ValueError("Amounts must be non-negative integer strings.")
        if event.get("destination_chain") and event["kind"] != "bridge":
            raise ValueError("Only bridge events declare a destination chain.")
        if event.get("destination_tx") and not event.get("destination_chain"):
            raise ValueError("Destination transaction requires a destination chain.")
        if event.get("destination_tx") and not event.get("correspondence_evidence"):
            raise ValueError("Cross-chain correspondence requires explicit evidence.")
        event["id"] = evidence_id(event)
        canonical = stable_json(event)
        if event["id"] in seen and seen[event["id"]] != canonical:
            raise ValueError("Conflicting observations share an event identity.")
        seen[event["id"]] = canonical
    data["events"] = [json.loads(v) for v in seen.values()]
    return data

def amount_display(event):
    raw = event["amount_raw"]
    d = event["asset"]["decimals"]
    if not d:
        return raw.lstrip("0") or "0"
    padded = raw.zfill(d + 1)
    return (padded[:-d].lstrip("0") or "0") + "." + padded[-d:]

def investigate(snapshot):
    data = validate_snapshot(snapshot)
    for event in data["events"]:
        event["amount_display"] = amount_display(event)
    boundaries = []
    for event in data["events"]:
        if event["kind"] == "bridge":
            boundaries.append({"event_id": event["id"], "type": "bridge", "label": "Bridge correspondence",
                "detail": "Destination correspondence is supplied in this snapshot; it has not been independently verified by this application." if event.get("destination_tx") else "A bridge interaction is recorded. Destination completion and recipient remain unresolved."})
        if event["kind"] == "service_boundary":
            boundaries.append({"event_id": event["id"], "type": "service", "label": "Service ledger boundary",
                "detail": "Service attribution is a source assertion. Customer identity and subsequent exchange withdrawals cannot be inferred from this path."})
    return {"snapshot": data, "boundaries": boundaries,
        "summary": {"event_count": len(data["events"]), "chain_count": len({e["chain"] for e in data["events"]}),
        "boundary_count": len(boundaries), "assessment": "Not assessed"},
        "finding": "Review the recorded path and its limits. No automated finding of laundering, fraud or insider activity has been made."}

def markdown_report(case):
    s = case["analysis"]["snapshot"]
    if not s["source"]["export_allowed"]:
        raise PermissionError("Export is disabled by this snapshot's source policy.")
    lines = ["# Investigation report", "", f"Case: {case['id']}", f"Title: {case['title']}",
        f"Subject: {s['subject']['chain']} / {s['subject']['address']}",
        f"Synthetic: {s['synthetic']}", f"Provider: {s['source']['provider']}",
        f"Retrieved: {s['source']['retrieved_at']}", f"Source: {s['source']['reference']}",
        f"Coverage: {s['coverage']['state']}", "", "## Evidence", ""]
    for e in s["events"]:
        lines.extend([f"- {e['id']} | {e['timestamp']} | {e['chain']} | {e['label']}",
            f"  {e['from']} -> {e['to']}; {e['amount_display']} {e['asset'].get('symbol', '')}; status: {e['status']}",
            f"  Transaction: {e['tx']}; locator: {e['locator']}"])
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in s["coverage"]["limitations"]]
    lines += [f"- {b['detail']}" for b in case["analysis"]["boundaries"]]
    lines += ["", "## Analyst history", ""]
    lines += [f"- {n['created_at']} | {n['disposition']}: {n['text']}" for n in case.get("notes", [])]
    lines += ["", case["analysis"]["finding"], ""]
    return "\n".join(lines)
