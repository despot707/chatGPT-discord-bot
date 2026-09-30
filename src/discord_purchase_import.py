"""Stage an operator-reviewed paid invoice for Discord purchase reconciliation.

This command cannot authenticate a financial export. Its caller must inspect the
actual paid invoice/transaction and net proceeds against independent evidence.
It never credits a ledger directly or changes paid-launch approval flags.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from src.discord_purchases import DiscordPurchases, Settlement, sku_map
from src.prepaid import Denied, Ledger

RECORD_FIELDS = {
    "receipt_id",
    "entitlement_id",
    "subscription_id",
    "guild_id",
    "sku_id",
    "product",
    "starts",
    "ends",
    "gross_micros",
    "net_micros",
    "currency",
}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-file", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--evidence-reference", required=True)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--confirm-paid-invoice", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_paid_invoice:
        parser.error("explicit paid invoice review is required")
    if args.record_file.stat().st_size > 8192:
        parser.error("record file exceeds 8 KiB")
    try:
        data = json.loads(args.record_file.read_text(encoding="utf-8"))
        if type(data) is not dict or set(data) != RECORD_FIELDS:
            raise ValueError("record must contain exactly the documented fields")
        app_id = int(os.environ["DISCORD_APPLICATION_ID"])
        mapping = sku_map(os.environ["DISCORD_SKU_MAP"])
        record = Settlement(
            **data,
            evidence_sha256=_hash_file(args.evidence_file),
            evidence_reference=args.evidence_reference,
            reviewed_by=args.reviewed_by,
        )
        purchases = DiscordPurchases(
            Ledger(os.getenv("PREPAID_DATABASE_PATH", "data/prepaid.sqlite3")),
            mapping,
            application_id=app_id,
        )
        inserted = purchases.import_reviewed_settlement(record)
    except (OSError, KeyError, TypeError, ValueError, Denied) as exc:
        parser.error(f"settlement was not staged: {exc}")
    print(
        "Staged reviewed settlement for authenticated Discord reconciliation."
        if inserted
        else "Reviewed settlement was already staged unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
