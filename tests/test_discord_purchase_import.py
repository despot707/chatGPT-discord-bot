import hashlib
import json
import sqlite3
import time

import pytest
from src.config import BotConfig
from src.discord_purchase_import import main
from src.prepaid import Ledger


def _record(**updates):
    now = int(time.time()) - 60
    result = {
        "receipt_id": "fake-receipt-001",
        "entitlement_id": 101,
        "subscription_id": 202,
        "guild_id": 303,
        "sku_id": 123,
        "product": "basic",
        "starts": now - 60,
        "ends": now + 30 * 86400,
        "gross_micros": 990000,
        "net_micros": 643500,
        "currency": "USD",
    }
    result.update(updates)
    return result


def _invoke(tmp_path, monkeypatch, record, *, confirm=True, evidence=True):
    record_path = tmp_path / "record.json"
    evidence_path = tmp_path / "invoice.bin"
    database_path = tmp_path / "ledger.sqlite3"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    if evidence:
        evidence_path.write_bytes(b"fake reviewed invoice evidence")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "456")
    monkeypatch.setenv("DISCORD_SKU_MAP", "123:basic")
    monkeypatch.setenv("PREPAID_DATABASE_PATH", str(database_path))
    args = [
        "--record-file",
        str(record_path),
        "--evidence-file",
        str(evidence_path),
        "--evidence-reference",
        "finance-export/fake-row-7",
        "--reviewed-by",
        "reviewer@example.invalid",
    ]
    if confirm:
        args.append("--confirm-paid-invoice")
    return main(args), database_path, evidence_path


def test_cli_stages_reviewed_record_and_never_credits_ledger(tmp_path, monkeypatch, capsys):
    def forbidden_credit(*args, **kwargs):
        raise AssertionError("import CLI must never credit the ledger")

    monkeypatch.setattr(Ledger, "credit", forbidden_credit)
    code, database_path, evidence_path = _invoke(tmp_path, monkeypatch, _record())

    assert code == 0
    assert "Staged reviewed settlement" in capsys.readouterr().out
    with sqlite3.connect(database_path) as db:
        row = db.execute(
            "SELECT facts FROM discord_purchase_settlement WHERE receipt_id=?",
            ("fake-receipt-001",),
        ).fetchone()
        assert db.execute("SELECT COUNT(*) FROM prepaid_grants").fetchone()[0] == 0
    facts = json.loads(row[0])
    assert facts["evidence_sha256"] == hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    assert facts["reviewed_by"] == "reviewer@example.invalid"
    assert facts["evidence_reference"] == "finance-export/fake-row-7"


def test_cli_requires_explicit_confirmation(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as error:
        _invoke(tmp_path, monkeypatch, _record(), confirm=False)
    assert error.value.code == 2
    database_path = tmp_path / "ledger.sqlite3"
    assert not database_path.exists()


@pytest.mark.parametrize(
    "record",
    [
        {**_record(), "unexpected": "extra"},
        {key: value for key, value in _record().items() if key != "receipt_id"},
        {**_record(), "gross_micros": 0},
        {**_record(), "net_micros": 1},
        {**_record(), "currency": "EUR"},
    ],
)
def test_cli_rejects_bad_record_without_staging(tmp_path, monkeypatch, record):
    with pytest.raises(SystemExit) as error:
        _invoke(tmp_path, monkeypatch, record)
    assert error.value.code == 2
    with sqlite3.connect(tmp_path / "ledger.sqlite3") as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_purchase_settlement'"
        ).fetchone()
        if table:
            assert db.execute("SELECT COUNT(*) FROM discord_purchase_settlement").fetchone()[0] == 0


def test_cli_rejects_malformed_json_and_missing_evidence(tmp_path, monkeypatch):
    record_path = tmp_path / "record.json"
    record_path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "456")
    monkeypatch.setenv("DISCORD_SKU_MAP", "123:basic")
    args = [
        "--record-file",
        str(record_path),
        "--evidence-file",
        str(tmp_path / "missing.bin"),
        "--evidence-reference",
        "fake-ref",
        "--reviewed-by",
        "reviewer",
    ]
    with pytest.raises(SystemExit) as malformed:
        main(args + ["--confirm-paid-invoice"])
    assert malformed.value.code == 2

    record_path.write_text(json.dumps(_record()), encoding="utf-8")
    with pytest.raises(SystemExit) as missing:
        main(args + ["--confirm-paid-invoice"])
    assert missing.value.code == 2
    assert not (tmp_path / "data" / "prepaid.sqlite3").exists()


def test_cli_replay_is_idempotent_but_changed_receipt_facts_are_denied(
    tmp_path, monkeypatch, capsys
):
    first = _invoke(tmp_path, monkeypatch, _record())
    assert first[0] == 0
    assert _invoke(tmp_path, monkeypatch, _record())[0] == 0
    assert "already staged unchanged" in capsys.readouterr().out

    changed = _record(net_micros=650000)
    with pytest.raises(SystemExit) as error:
        _invoke(tmp_path, monkeypatch, changed)
    assert error.value.code == 2
    with sqlite3.connect(first[1]) as db:
        rows = db.execute("SELECT facts FROM discord_purchase_settlement").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["net_micros"] == 643500


def test_purchase_config_defaults_off_and_validates_mode_and_observe_requirements():
    assert BotConfig.from_env({"DISCORD_BOT_TOKEN": "fake-token"}).discord_purchase_mode == "off"
    with pytest.raises(ValueError, match="Invalid Discord purchase mode"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "fake-token", "DISCORD_PURCHASE_MODE": "surprise"})
    for extra in (
        {"DISCORD_PURCHASE_MODE": "observe"},
        {"DISCORD_PURCHASE_MODE": "observe", "DISCORD_APPLICATION_ID": "456"},
        {"DISCORD_PURCHASE_MODE": "observe", "DISCORD_SKU_MAP": "123:basic"},
    ):
        with pytest.raises(ValueError, match="application ID and SKU map"):
            BotConfig.from_env({"DISCORD_BOT_TOKEN": "fake-token", **extra})

    config = BotConfig.from_env(
        {
            "DISCORD_BOT_TOKEN": "fake-token",
            "DISCORD_PURCHASE_MODE": "observe",
            "DISCORD_APPLICATION_ID": "456",
            "DISCORD_SKU_MAP": "123:basic",
        }
    )
    assert config.discord_purchase_mode == "observe"
    assert config.discord_application_id == 456
    assert config.discord_sku_map == "123:basic"
