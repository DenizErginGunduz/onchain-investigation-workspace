"""Bounded RPC corroboration; never infer a trade's meaning from a receipt alone."""
import re

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
HASH = re.compile(r"0x[0-9a-fA-F]{64}")


def quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value):
        raise ValueError("Invalid RPC quantity")
    return int(value, 16)


def reconcile(tx_hash, wallet, receipt, transaction, block, head, chain_id):
    """Corroborate one transaction using a separately acquired RPC response set."""
    if not HASH.fullmatch(tx_hash) or not ADDRESS.fullmatch(wallet):
        raise ValueError("Invalid transaction or wallet")
    if quantity(chain_id) != 137:
        raise ValueError("RPC is not Polygon PoS")
    result = {"tx": tx_hash.lower(), "chain": "eip155:137", "state": "unresolved",
              "transfers": [], "limitations": [
                  "Corroborated against one RPC source, not a cryptographic proof or multi-provider consensus.",
                  "Receipt success does not verify every provider activity field or trade amount.",
                  "Only ERC-20-shaped Transfer logs involving the subject are decoded; token identity, decimals and semantics are not assumed.",
                  "No wallet ownership, service attribution, intent, bridge delivery or subsequent exchange withdrawal is inferred."]}
    if receipt is None or transaction is None:
        result["reason"] = "Transaction or receipt unavailable; may be pending or outside source coverage."
        return result
    if not all(isinstance(x, dict) for x in (receipt, transaction, block)):
        raise ValueError("Incomplete RPC evidence")
    expected = tx_hash.lower()
    if receipt.get("transactionHash", "").lower() != expected or transaction.get("hash", "").lower() != expected:
        raise ValueError("Transaction hash mismatch")
    block_hash = receipt.get("blockHash", "")
    if not HASH.fullmatch(block_hash) or any(x.get("blockHash", "").lower() != block_hash.lower() for x in (transaction,)):
        raise ValueError("Block hash mismatch")
    if block.get("hash", "").lower() != block_hash.lower():
        raise ValueError("Canonical block differs from receipt (possible reorganization)")
    number = quantity(receipt.get("blockNumber"))
    if number != quantity(transaction.get("blockNumber")) or number != quantity(block.get("number")):
        raise ValueError("Block number mismatch")
    index = quantity(receipt.get("transactionIndex"))
    if index != quantity(transaction.get("transactionIndex")):
        raise ValueError("Transaction index mismatch")
    hashes = block.get("transactions")
    if not isinstance(hashes, list) or index >= len(hashes) or not isinstance(hashes[index], str) or hashes[index].lower() != expected:
        raise ValueError("Transaction missing from canonical block position")
    confirmations = quantity(head) - number + 1
    if confirmations < 1:
        raise ValueError("Head is behind receipt block")
    status = quantity(receipt.get("status"))
    if status not in (0, 1):
        raise ValueError("Invalid receipt status")
    logs = receipt.get("logs")
    if not isinstance(logs, list) or len(logs) > 10000:
        raise ValueError("Invalid receipt logs")
    seen = set()
    for log in logs:
        if not isinstance(log, dict) or log.get("removed") is not False:
            raise ValueError("Removed or unspecified log state")
        if log.get("transactionHash", "").lower() != expected or log.get("blockHash", "").lower() != block_hash.lower():
            raise ValueError("Log belongs to a different transaction or block")
        if quantity(log.get("blockNumber")) != number or quantity(log.get("transactionIndex")) != index:
            raise ValueError("Log position mismatch")
        locator = quantity(log.get("logIndex"))
        if locator in seen:
            raise ValueError("Duplicate log index")
        seen.add(locator)
        topics = log.get("topics")
        if not isinstance(topics, list) or len(topics) > 4 or not all(isinstance(t, str) and HASH.fullmatch(t) for t in topics):
            raise ValueError("Invalid log topics")
        if len(topics) != 3 or topics[0].lower() != TRANSFER:
            continue
        if not all(isinstance(t, str) and re.fullmatch(r"0x0{24}[0-9a-fA-F]{40}", t) for t in topics[1:]):
            raise ValueError("Malformed Transfer address topics")
        if not ADDRESS.fullmatch(log.get("address", "")) or not HASH.fullmatch(log.get("data", "")):
            raise ValueError("Malformed Transfer payload")
        sender, recipient = ("0x" + t[-40:].lower() for t in topics[1:])
        if wallet.lower() not in (sender, recipient):
            continue
        result["transfers"].append({"locator": "log:" + str(locator), "contract": log["address"].lower(),
                                    "from": sender, "to": recipient, "amount_raw": str(int(log["data"], 16)),
                                    "decimals": None, "interpretation": "ERC-20-shaped Transfer log; contract semantics not independently audited"})
    if status == 0 and logs:
        raise ValueError("Failed receipt unexpectedly contains logs")
    result.update(state="rpc_corroborated", execution="success" if status else "reverted",
                  block_number=number, block_hash=block_hash.lower(), block_timestamp=quantity(block.get("timestamp")),
                  confirmations_at_retrieval=confirmations, transaction_index=index,
                  subject_in_transfer_logs=bool(result["transfers"]))
    return result
