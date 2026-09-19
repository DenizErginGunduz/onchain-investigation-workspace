"""One bounded, read-only connectivity probe. Never relax certificate checks."""
import json
import re
import ssl
import urllib.error
from datetime import datetime, timezone
from .providers import PolymarketProvider, ProviderUnavailable


def check_public_source():
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "provider": "Polymarket public activity v2", "tls_verification": True,
              "purpose": "Connectivity/schema probe using a nonzero sentinel address; not an investigation or ownership claim."}
    try:
        snapshot = PolymarketProvider().fetch("0x" + "1" * 40)
        result.update(status="reachable", observations=len(snapshot["events"]),
                      limitation="Successful retrieval does not independently verify chain evidence.")
    except ProviderUnavailable as exc:
        result.update(status="unavailable", code=exc.code, message=str(exc))
        # Distinguish an upstream HTTP rejection from DNS/TLS failure without
        # logging response bodies, request headers or credentials.
        cause = exc.__cause__
        if isinstance(cause, urllib.error.HTTPError):
            result.update(code="http_error", http_status=cause.code,
                          message="The public source returned an HTTP error. No data was saved.")
            try:
                details = json.loads(cause.read(4097))
                for field in ("code", "parameter"):
                    value = details.get(field)
                    if isinstance(value, str) and re.fullmatch(r"[a-zA-Z_]{1,64}", value):
                        result["upstream_" + field] = value
            except (ValueError, AttributeError, OSError):
                pass
    result["trusted_ca_count"] = ssl.create_default_context().cert_store_stats()["x509_ca"]
    return result


if __name__ == "__main__":
    result = check_public_source()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "reachable" else 1)
