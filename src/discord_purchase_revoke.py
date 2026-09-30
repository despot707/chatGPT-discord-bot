"""Record a reviewed refund, chargeback, or correction of a Discord invoice.

This operator command hashes private evidence and permanently denies the receipt.
It cannot authenticate the financial document; review it independently first.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from src.discord_purchases import DiscordPurchases, sku_map
from src.prepaid import Denied, Ledger


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument(
        "--reason", choices=("refund", "chargeback", "payment_reversal"), required=True
    )
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--evidence-reference", required=True)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--confirm-revocation", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_revocation:
        parser.error("explicit financial revocation review is required")
    try:
        evidence_sha256 = _hash_file(args.evidence_file)
        app_id = int(os.environ["DISCORD_APPLICATION_ID"])
        mapping = sku_map(os.environ["DISCORD_SKU_MAP"])
        purchases = DiscordPurchases(
            Ledger(os.getenv("PREPAID_DATABASE_PATH", "data/prepaid.sqlite3")),
            mapping,
            application_id=app_id,
        )
        applied = purchases.invalidate_settlement(
            args.receipt_id,
            args.reason,
            evidence_sha256,
            args.evidence_reference,
            args.reviewed_by,
        )
    except (OSError, KeyError, TypeError, ValueError, Denied) as exc:
        parser.error(f"receipt was not revoked: {exc}")
    print(
        "Reviewed financial revocation recorded; receipt access is denied."
        if applied
        else "Reviewed financial revocation was already recorded unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
