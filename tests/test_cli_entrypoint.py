"""极简统一主入口 QuantCLI 与跨进程状态持久化单元测试。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.configs.settings import Settings
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.repositories.quote import QuoteRepository
from src.runtime.cli import QuantCLI


def _test_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=str(tmp_path),
        timesfm_enabled=False,
        news_enabled=False,
        live_quotes_enabled=False,
        jd_proactive_auth_enabled=False,
    )


def _seed_quote(tmp_path: Path, quote_id: str = "cli_q_1") -> None:
    now = datetime.now(UTC)
    repo = QuoteRepository(storage_dir=tmp_path)
    repo.save_quote(
        QuoteRaw(
            quote_id=quote_id,
            source_id="manual_bank",
            bid_price=600.0,
            ask_price=600.4,
            quote_at=now,
            received_at=now,
            available_at=now,
            is_tradable=True,
            quality_state=DataQualityState.NORMAL,
            product_code="GOLD_ACCUMULATION",
        )
    )


def test_cli_exposes_only_five_core_commands() -> None:
    parser = QuantCLI.build_parser()
    sub_action = next(action for action in parser._actions if action.dest == "command")
    assert sub_action.choices is not None
    assert set(sub_action.choices) == {"serve", "backtest", "paper", "live", "auth"}


def test_cli_default_invokes_serve(tmp_path: Path) -> None:
    settings = _test_settings(tmp_path)
    with patch("src.runtime.cli.uvicorn.run") as mock_uvicorn:
        code = QuantCLI.run([], settings=settings)
    assert code == 0
    mock_uvicorn.assert_called_once_with(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


def test_cli_backtest_zero_args_and_date_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _test_settings(tmp_path)
    code = QuantCLI.run(["backtest", "--start", "2026-11-01", "--end", "2026-11-24", "--json"], settings=settings)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["product_code"] == settings.default_product_code
    assert payload["quote_count"] == 0
    assert payload["shadow_report"]["initial_cash"] == settings.default_initial_cash


def test_cli_paper_status_manual_buy_and_jd_confirm_guard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _test_settings(tmp_path)
    _seed_quote(tmp_path)

    assert QuantCLI.run(["paper", "status", "--engine", "local"], settings=settings) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["engine"] == "local"
    assert status["kill_switch_active"] is False

    # 未传 --confirm 时向京东模拟盘下单必须失败返回非零退出码
    fail_code = QuantCLI.run(["paper", "buy", "--engine", "jd", "--grams", "1"], settings=settings)
    assert fail_code == 1
    assert "显式确认" in capsys.readouterr().out

    # 本地模拟盘买入后，跨调用仍保留持仓与订单流水
    assert QuantCLI.run(["paper", "buy", "--grams", "2"], settings=settings) == 0
    buy_out = json.loads(capsys.readouterr().out)
    assert buy_out["fill"]["is_filled"] is True

    assert QuantCLI.run(["paper", "status"], settings=settings) == 0
    paper_status = json.loads(capsys.readouterr().out)
    assert paper_status["order_count"] == 1
    assert paper_status["snapshot"]["gold_total_g"] == pytest.approx(2.0)


def test_cli_live_reconcile_merged_and_cross_process_persistence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _test_settings(tmp_path)
    assert QuantCLI.run(["live", "status"], settings=settings) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["risk_state"] == "NORMAL"
    assert status["kill_switch_active"] is False

    # 1. 快照对账一致返回 0
    ok_code = QuantCLI.run(["live", "reconcile", "--cash", "100000", "--gold", "0", "--json"], settings=settings)
    assert ok_code == 0
    assert json.loads(capsys.readouterr().out)["report"]["matched"] is True

    # 2. 快照对账不一致返回 2 并持久化 HALT_RECONCILE 与 Kill-Switch
    mismatch_code = QuantCLI.run(["live", "reconcile", "--cash", "90000", "--gold", "0", "--json"], settings=settings)
    assert mismatch_code == 2
    mismatch_payload = json.loads(capsys.readouterr().out)
    assert mismatch_payload["recommended_risk_state"] == "HALT_RECONCILE"
    assert mismatch_payload["kill_switch_active"] is True

    # 3. 跨调用恢复熔断状态，resume 后恢复 NORMAL
    assert QuantCLI.run(["live", "status"], settings=settings) == 0
    persisted = json.loads(capsys.readouterr().out)
    assert persisted["risk_state"] == "HALT_RECONCILE"
    assert persisted["kill_switch_active"] is True

    assert QuantCLI.run(["live", "resume", "--json"], settings=settings) == 0
    assert json.loads(capsys.readouterr().out)["kill_switch_active"] is False

    # 4. 本地模拟买入不应污染实盘对账，实盘账本自身存在未匹配订单时逐笔流水对账返回 2
    _seed_quote(tmp_path)
    assert QuantCLI.run(["paper", "buy", "--grams", "1"], settings=settings) == 0
    capsys.readouterr()

    stmt_file = tmp_path / "statements.json"
    stmt_file.write_text("[]", encoding="utf-8")
    assert QuantCLI.run(["live", "reconcile", "--statements-file", str(stmt_file), "--json"], settings=settings) == 0
    assert json.loads(capsys.readouterr().out)["report"]["matched"] is True

    from src.runtime.dependencies import RuntimeDependencies

    runtime = RuntimeDependencies(settings)
    runtime.ledger.record_buy(client_order_id="live_cli_1", grams=1.0, price=600.0, fee=0.0)
    assert QuantCLI.run(["live", "reconcile", "--statements-file", str(stmt_file), "--json"], settings=settings) == 2
    recon_out = json.loads(capsys.readouterr().out)
    assert recon_out["report"]["matched"] is False
    assert recon_out["kill_switch_active"] is True

    # 5. 同时传入快照与流水参数时返回错误码 1
    both_err = QuantCLI.run(
        ["live", "reconcile", "--cash", "100000", "--gold", "0", "--statements-file", str(stmt_file)],
        settings=settings,
    )
    assert both_err == 1
    assert "不可同时指定" in capsys.readouterr().out
