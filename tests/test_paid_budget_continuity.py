"""Offline commercial accounting regressions; original personal limits stay intact."""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from src.budget import BudgetError, BudgetExceeded, BudgetLedger, BudgetPolicy
from src.prepaid import COSTS, Denied
from src.prepaid_gateway import Gateway
from src.prepaid_runtime import Runtime

from tests.test_discord_purchases import APP, GUILD, NOW, LiveGuildClient, purchases

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Commercial accounting exercises the real Linux single-writer lease; verified in Linux CI.",
)


def configure(monkeypatch, tmp_path):
    for name, value in {
        "PREPAID_MODE": "enforce",
        "DISCORD_PURCHASE_MODE": "enforce",
        "DISCORD_FUNDING_MODE": "entitlement",
        "DISCORD_APPLICATION_ID": str(APP),
        "DISCORD_SKU_MAP": "111:basic",
        "HARD_BUDGET_ENABLED": "true",
        "BUDGET_TIMEZONE": "UTC",
        "BUDGET_DATABASE_PATH": str(tmp_path / "budget.sqlite3"),
        "BUDGET_OPENING_MONTH_SPEND_USD": "10",
    }.items():
        monkeypatch.setenv(name, value)


async def setup_paid(tmp_path, monkeypatch, *, product="basic"):
    configure(monkeypatch, tmp_path)
    p = purchases(tmp_path)
    p.mapping = {111: product}
    monkeypatch.setenv("DISCORD_SKU_MAP", f"111:{product}")
    await p.reconcile(LiveGuildClient(), credit=True)
    rt = Runtime(p.ledger, str(tmp_path / "unused.json"), clock=lambda: NOW)
    client = NS(
        responses=NS(
            input_tokens=NS(count=AsyncMock(return_value=NS(input_tokens=500))),
            create=AsyncMock(
                return_value=NS(
                    output_text="answer",
                    output=[],
                    model="gpt-6-luna",
                    status="completed",
                    usage=NS(input_tokens=500, output_tokens=100),
                )
            ),
        )
    )
    rt.ready()
    return rt, Gateway(p.ledger, client, ready=rt.ready, budget=rt.global_budget()), client


def rows(path):
    with sqlite3.connect(path) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("periods", "reservations", "charges")
        }


async def test_paid_basic_all_400_units_survive_exhausted_personal_pool(tmp_path, monkeypatch):
    rt, gateway, client = await setup_paid(tmp_path, monkeypatch)
    for _ in range(400):
        assert (
            await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}]) == "answer"
        )
    with pytest.raises(Denied, match="exhausted"):
        await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}])
    assert client.responses.create.await_count == 400
    snapshot = rt.global_budget().snapshot()
    assert snapshot["accounting_mode"] == "paid-entitlement-v1"
    assert snapshot["cumulative_spent_micros"] == 10_000_000 + 400 * 113
    assert snapshot["reserved_micros"] == 0


async def test_migration_preserves_periods_charges_unknown_holds_and_restart(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    clock = [datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=32)]
    path = tmp_path / "budget.sqlite3"
    legacy = BudgetLedger(BudgetPolicy(timezone="UTC"), path, 12345, clock=lambda: clock[0])
    settled = legacy.reserve("luna", 1500)
    legacy.settle(settled.id, 100)
    pending = legacy.reserve("extras", 1000)
    clock[0] = datetime.fromtimestamp(NOW, timezone.utc)
    legacy.snapshot()
    before = rows(path)
    rt, gateway, _ = await setup_paid(tmp_path, monkeypatch)
    assert rows(path) == before
    assert rt.global_budget().snapshot()["cumulative_spent_micros"] == 12445
    assert rt.global_budget().snapshot()["reserved_micros"] == 1000
    with pytest.raises(BudgetError, match="accounting mode"):
        legacy.reserve("luna", 1)
    with pytest.raises(BudgetError, match="accounting mode"):
        BudgetLedger(BudgetPolicy(timezone="UTC"), path)
    rt._lease.close()
    restarted = Runtime(rt.ledger, rt.approval_path, clock=lambda: NOW)
    restarted.ready()
    assert rows(path) == before
    restarted.global_budget().settle(pending.id, 900)
    # Legacy cross-month charges are duplicated for personal monthly admission;
    # cumulative commercial reporting must count that actual only once.
    assert restarted.global_budget().snapshot()["cumulative_spent_micros"] == 13345
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM paid_budget_migrations").fetchone()[0] == 1
    assert (
        await Gateway(
            rt.ledger, gateway.client, ready=restarted.ready, budget=restarted.global_budget()
        ).complete(GUILD, 777, [{"role": "user", "content": "hello"}])
        == "answer"
    )


@pytest.mark.parametrize("locked", [False, True])
async def test_unknown_baseline_or_existing_lock_not_erased(tmp_path, monkeypatch, locked):
    configure(monkeypatch, tmp_path)
    monkeypatch.delenv("BUDGET_OPENING_MONTH_SPEND_USD")
    path = tmp_path / "budget.sqlite3"
    legacy = BudgetLedger(
        BudgetPolicy(timezone="UTC"),
        path,
        0 if locked else None,
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc),
    )
    if locked:
        legacy.lock("existing_usage_unknown")
    p = purchases(tmp_path)
    await p.reconcile(LiveGuildClient(), credit=True)
    rt = Runtime(p.ledger, "unused", clock=lambda: NOW)
    rt.ready()
    budget = rt.global_budget()
    before = rows(path)
    rid = p.ledger.reserve(GUILD, 777, "chat")
    with pytest.raises(BudgetExceeded):
        budget.reserve_paid(p.ledger, [rid])
    assert rows(path) == before
    assert budget.snapshot()["lock_reason"] == ("existing_usage_unknown" if locked else None)


async def test_paid_ledger_rejects_unscoped_unbounded_and_wrong_policy_requests(
    tmp_path, monkeypatch
):
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    with pytest.raises(BudgetError, match="paid"):
        rt.global_budget().reserve("luna", 1)
    with pytest.raises(BudgetError, match="paid"):
        rt.global_budget().reserve_paid(rt.ledger, ["missing"])
    rt._lease.close()
    monkeypatch.setenv("BUDGET_LUNA_USD", "6")
    second = Runtime(rt.ledger, rt.approval_path, clock=lambda: NOW)
    with pytest.raises(BudgetError, match="policy changed"):
        second.ready()


async def test_stale_snapshot_during_token_count_never_dispatches(tmp_path, monkeypatch):
    rt, gateway, client = await setup_paid(tmp_path, monkeypatch)

    async def count(**kwargs):
        rt.clock = lambda: NOW + 10000
        return NS(input_tokens=500)

    client.responses.input_tokens.count.side_effect = count
    with pytest.raises(Denied):
        await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}])
    client.responses.create.assert_not_called()
    assert rt.ledger.summary(GUILD)["remaining"]["chat"] == 400
    assert rt.global_budget().snapshot()["reserved_micros"] == 0


@pytest.mark.parametrize("failure", ["timeout", "usage", "overrun", "after_dispatch_commit"])
async def test_uncertain_outcomes_hold_and_contract_violations_lock(tmp_path, monkeypatch, failure):
    rt, gateway, client = await setup_paid(tmp_path, monkeypatch)
    if failure == "timeout":
        client.responses.create.side_effect = TimeoutError()
    elif failure == "usage":
        client.responses.create.return_value.usage = None
    elif failure == "overrun":
        client.responses.create.return_value.usage.output_tokens = 501
    else:
        dispatch = rt.ledger.dispatch

        def interrupted(rid):
            dispatch(rid)
            raise RuntimeError("interrupted after durable dispatch")

        monkeypatch.setattr(rt.ledger, "dispatch", interrupted)
    with pytest.raises((TimeoutError, Denied, RuntimeError)):
        await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}])
    assert rt.ledger.summary(GUILD)["remaining"]["chat"] == 399
    assert rt.global_budget().snapshot()["reserved_micros"] == COSTS["chat"]
    assert rt.global_budget().snapshot()["locked"] is (failure in {"usage", "overrun"})


async def test_actual_client_startup_and_restart_do_not_open_legacy_provider(tmp_path, monkeypatch):
    from src import prepaid_runtime as pr
    from src.bot import DiscordClient
    from src.config import BotConfig

    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    monkeypatch.setattr(pr, "_INSTANCE", rt)
    config = BotConfig(
        discord_bot_token="x",
        hard_budget_enabled=True,
        discord_purchase_mode="enforce",
        discord_application_id=APP,
        discord_sku_map="111:basic",
        chat_database_path=None,
        gaming_database_path=str(tmp_path / "gaming.sqlite3"),
    )
    for _ in range(2):
        client = DiscordClient(config)
        assert client.provider_manager.legacy.budget is None
        assert all(
            not hasattr(provider, "client")
            for provider in client.provider_manager.legacy.providers.values()
        )
        client.provider_manager.rt.ready()
        assert rt.global_budget().snapshot()["cumulative_spent_micros"] == 10_000_000
        await client.close()
        rt._lease.close()
        rt = Runtime(rt.ledger, rt.approval_path, clock=lambda: NOW)
        monkeypatch.setattr(pr, "_INSTANCE", rt)


async def test_operator_planning_report_is_alert_only_and_keeps_orphan_holds(tmp_path, monkeypatch):
    rt, gateway, _ = await setup_paid(tmp_path, monkeypatch)
    orphan = rt.ledger.reserve(GUILD, 777, "chat")
    before = rows(rt.global_budget().path)
    report = rt.planning_report(hosting_micros=90_000_000)
    assert report["baseline_micros"] == 100_000_000
    assert report["incurred_micros"] == 10_000_000
    assert report["unresolved_micros"] == 1500
    assert report["remaining_quota_micros"] == 399 * 1500
    assert report["planned_micros"] == 100_600_000
    assert report["warning"] is True
    assert report["admission_paused"] is False
    assert rows(rt.global_budget().path) == before
    assert await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}]) == "answer"
    rt.ledger.cancel(orphan)


async def test_migration_keeps_verified_backup_once_and_unknown_hosting_is_incomplete(
    tmp_path, monkeypatch
):
    configure(monkeypatch, tmp_path)
    path = tmp_path / "budget.sqlite3"
    old = BudgetLedger(
        BudgetPolicy(timezone="UTC"),
        path,
        100,
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc),
    )
    old.reserve("luna", 1500)
    before = rows(path)
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        backup, digest = db.execute(
            "SELECT backup_path,backup_sha256 FROM paid_budget_migrations"
        ).fetchone()
    import hashlib
    from pathlib import Path

    assert rows(backup) == before
    assert hashlib.sha256(Path(backup).read_bytes()).hexdigest() == digest
    report = rt.planning_report()
    assert report["complete"] is False
    assert report["planned_micros"] is None and report["warning"] is None
    assert report["settled_receipts_micros"] is None
    rt._lease.close()
    Runtime(rt.ledger, rt.approval_path, clock=lambda: NOW).ready()
    assert len(list(tmp_path.glob("*.before-paid-v1-*.sqlite3"))) == 1


async def test_monthly_planning_does_not_compare_lifetime_spend_to_monthly_baseline(
    tmp_path, monkeypatch
):
    configure(monkeypatch, tmp_path)
    clock = [datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=32)]
    old = BudgetLedger(
        BudgetPolicy(timezone="UTC"),
        tmp_path / "budget.sqlite3",
        150_000_000,
        clock=lambda: clock[0],
    )
    clock[0] = datetime.fromtimestamp(NOW, timezone.utc)
    old.snapshot()
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    report = rt.planning_report(hosting_micros=10_000_000)
    assert report["incurred_micros"] == 0
    assert report["cumulative_spent_micros"] == 150_000_000
    assert report["planned_micros"] == 10_600_000
    assert report["warning"] is False
    assert report["period"] == clock[0].strftime("%Y-%m")
    assert report["as_of"] == clock[0].isoformat()


async def test_paid_metadata_supports_real_status_provider_commands_without_sdk(
    tmp_path, monkeypatch
):
    from src import prepaid_runtime as pr
    from src.bot import DiscordClient
    from src.config import BotConfig
    from src.providers import ProviderError, ProviderType

    from tests.test_bot import interaction

    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    monkeypatch.setattr(pr, "_INSTANCE", rt)
    client = DiscordClient(
        BotConfig(
            discord_bot_token="x",
            hard_budget_enabled=True,
            discord_purchase_mode="enforce",
            discord_application_id=APP,
            discord_sku_map="111:basic",
            chat_database_path=None,
        )
    )
    client._register_commands()
    target = interaction()
    await client.tree.get_command("status").callback(target)
    assert "gpt-6-luna" in target.response.send_message.call_args.args[0]
    await client.tree.get_command("provider").callback(target)
    assert "Available providers: openai" in target.response.send_message.call_args.args[0]
    await client.tree.get_command("provider").callback(target, "openai", "gpt-6-luna")
    assert "Provider set to openai" in target.response.send_message.call_args.args[0]
    provider = client.provider_manager.get_provider(ProviderType.OPENAI)
    assert not hasattr(provider, "client")
    assert [m.name for m in client.provider_manager.get_provider_models(ProviderType.OPENAI)] == [
        "gpt-6-luna"
    ]
    with pytest.raises(ProviderError, match="gateway"):
        await provider.chat_completion([])
    with pytest.raises(ProviderError, match="gateway"):
        await provider.generate_image("unscoped")
    client.web_service = client.web_service.legacy
    await client.close()


async def test_paid_chat_survives_migrated_daily_exhaustion(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    old = BudgetLedger(
        BudgetPolicy(timezone="UTC"),
        tmp_path / "budget.sqlite3",
        0,
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc),
    )
    daily = old.snapshot()["luna"]["daily_remaining_micros"]
    old.reserve("luna", daily)
    rt, gateway, _ = await setup_paid(tmp_path, monkeypatch)
    assert await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}]) == "answer"
    assert rt.global_budget().snapshot()["reserved_micros"] == daily


@pytest.mark.parametrize("feature", ["chat", "reasoning", "search", "images", "optional_web"])
async def test_every_commercial_feature_holds_exact_maximum_before_sdk(
    tmp_path, monkeypatch, feature
):
    import base64

    rt, gateway, client = await setup_paid(tmp_path, monkeypatch, product="premium")
    expected = COSTS[feature] if feature != "optional_web" else COSTS["chat"] + COSTS["search"]
    result = client.responses.create.return_value
    if feature in {"search", "optional_web"}:
        result.output = [NS(type="web_search_call", status="completed")]

    async def checked_create(**kwargs):
        assert rt.global_budget().snapshot()["reserved_micros"] == expected
        with rt.ledger.db() as db:
            records = db.execute("SELECT state,reserved FROM prepaid_requests").fetchall()
        assert all(record["state"] == "dispatched" for record in records)
        assert sum(record["reserved"] for record in records) == expected
        return result

    client.responses.create.side_effect = checked_create
    if feature == "images":
        result = NS(
            usage=NS(input_tokens=100, output_tokens=439),
            data=[NS(b64_json=base64.b64encode(b"jpeg").decode())],
        )
        client.images = NS(generate=AsyncMock(side_effect=checked_create))
        assert await gateway.image(GUILD, 777, "a cat") == b"jpeg"
    else:
        assert (
            await gateway.complete(
                GUILD,
                777,
                [{"role": "user", "content": "hello"}],
                reasoning=feature == "reasoning",
                search=feature == "search",
                allow_web=feature == "optional_web",
            )
            == "answer"
        )
    assert rt.global_budget().snapshot()["reserved_micros"] == 0


@pytest.mark.parametrize("change", ["disabled", "invalidated", "revoked"])
async def test_token_count_race_rechecks_permission_and_releases_both_ledgers(
    tmp_path, monkeypatch, change
):
    rt, gateway, client = await setup_paid(tmp_path, monkeypatch, product="premium")

    async def count(**kwargs):
        if change == "disabled":
            monkeypatch.setenv("AI_ACCESS_MODE", "disabled")
        else:
            with rt.ledger.db() as db:
                if change == "invalidated":
                    db.execute(
                        "DELETE FROM discord_purchase_state WHERE key='complete_snapshot_at'"
                    )
                else:
                    db.execute("UPDATE prepaid_grants SET revoked=1")
        return NS(input_tokens=500)

    client.responses.input_tokens.count.side_effect = count
    with pytest.raises(Denied):
        await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}], allow_web=True)
    client.responses.create.assert_not_called()
    assert rt.global_budget().snapshot()["reserved_micros"] == 0
    with rt.ledger.db() as db:
        assert {row[0] for row in db.execute("SELECT state FROM prepaid_requests")} == {"cancelled"}


async def test_paid_cash_hold_unique_and_interruption_before_cash_keeps_quota(
    tmp_path, monkeypatch
):
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    rid = rt.ledger.reserve(GUILD, 777, "chat")
    # A crash here leaves the unit held, even though cash admission has not run.
    assert rt.planning_report(hosting_micros=0)["unresolved_micros"] == 1500
    budget = rt.global_budget()
    budget.reserve_paid(rt.ledger, [rid])
    with pytest.raises(BudgetError):
        budget.reserve_paid(rt.ledger, [rid])
    assert budget.snapshot()["reserved_micros"] == 1500
    assert rt.ledger.summary(GUILD)["remaining"]["chat"] == 399


async def test_backup_failure_leaves_original_accounting_and_mode_untouched(tmp_path, monkeypatch):
    from src.budget import PaidBudgetLedger

    configure(monkeypatch, tmp_path)
    path = tmp_path / "budget.sqlite3"
    old = BudgetLedger(
        BudgetPolicy(timezone="UTC"),
        path,
        1234,
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc),
    )
    old.reserve("luna", 1500)
    before = rows(path)

    def fail_backup(*args):
        raise BudgetError("Paid migration backup failed")

    monkeypatch.setattr(PaidBudgetLedger, "_backup_before_migration", fail_backup)
    with pytest.raises(BudgetError, match="backup failed"):
        await setup_paid(tmp_path, monkeypatch)
    assert rows(path) == before
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT value FROM ledger_state WHERE key='accounting_mode'").fetchone()[0]
            == "personal-v1"
        )
