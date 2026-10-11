"""계산 로직 단위 테스트. 아래 숫자는 모두 테스트용 합성 데이터이며 실제 시세가 아닙니다."""
import pandas as pd

from aisec import backtest, indicators, market, plan, risk, setups
from aisec.cli import build_universe
from aisec.data import is_preferred

RISK = {"capital": 10_000_000, "risk_per_trade_pct": 1, "max_position_pct": 10, "max_total_exposure_pct": 60,
        "min_rr": 2.0, "stop_loss_pct": 7, "max_atr_pct": 5}


def _frame(closes, volumes=None, spread=0.01):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"시가": c, "고가": c * (1 + spread), "저가": c * (1 - spread), "종가": c,
                         "거래량": volumes or [1000] * len(closes)}, index=idx)


def test_snapshot_uptrend():
    snap = indicators.snapshot(_frame([100 + i for i in range(150)]))
    assert snap["ma20"] > snap["ma60"] and snap["rsi14"] > 90


def test_breakout_detected_with_volume():
    closes = [100.0] * 149 + [110.0]           # 횡보하다 마지막 날 10% 돌파
    vols = [1000] * 149 + [3000]               # 거래량 3배
    found = {s["setup"] for s in setups.detect(setups.features(_frame(closes, vols)).iloc[-1])}
    assert "돌파" in found


def test_breakout_needs_volume():
    closes = [100.0] * 149 + [110.0]
    found = {s["setup"] for s in setups.detect(setups.features(_frame(closes)).iloc[-1])}
    assert "돌파" not in found


def test_trend_setup_and_stop_below_price():
    f = setups.features(_frame([100 * 1.004 ** i for i in range(200)]))
    found = setups.detect(f.iloc[-1])
    assert "추세지속" in {s["setup"] for s in found}
    assert all(s["stop"] < f.iloc[-1]["close"] for s in found)


def test_plan_reward_to_risk():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, target_rr=2.0)
    assert p["target"] == 11000 and p["rr"] == 2.0 and p["stop_pct"] == -5.0


def test_risk_gate_sizes_by_one_percent_rule():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, 2.0)
    g = risk.gate(p, atr_pct=2, market={"shock": False}, risk=RISK, exposure=0)
    # 1% = 10만원, 주당 손실 500원 → 200주지만 비중 10%(100만원) 한도에 걸려 100주
    assert g["shares"] == 100 and g["verdict"].startswith("통과")


def test_risk_gate_blocks_on_market_shock_and_wide_stop():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9000, "why": ""}, 2.0)   # 손절폭 -10%
    g = risk.gate(p, atr_pct=2, market={"shock": True}, risk=RISK, exposure=0)
    failed = {c["name"] for c in g["checks"] if not c["pass"]}
    assert failed == {"04 최대손실", "05 변동성"} and g["verdict"].startswith("차단")


def test_risk_gate_blocks_total_exposure():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, 2.0)
    g = risk.gate(p, atr_pct=2, market={"shock": False}, risk=RISK, exposure=5_500_000)
    assert not g["checks"][1]["pass"]


def test_market_regime_and_shock():
    up = market.regime(_frame([2000 + 5 * i for i in range(100)]))
    assert up["regime"] == "상승장" and not up["shock"]
    crash = market.regime(_frame([2500] * 99 + [2400]))   # 하루 -4%
    assert crash["shock"]


def test_exit_counts_stop_first_when_both_hit():
    bars = _frame([100, 100], spread=0.2)   # 하루에 ±20% 흔들림 → 손절·목표 모두 닿음
    assert backtest.simulate_exit(bars, 100, 90, 110, 20)["result"] == "손절"


def test_stats_win_rate_and_drawdown():
    d1, d2, d3 = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    trades = [{"date": d1, "ret_pct": 10, "r": 2}, {"date": d2, "ret_pct": -5, "r": -1},
              {"date": d3, "ret_pct": -5, "r": -1}]
    s = backtest.stats(trades)
    assert s["win_rate_pct"] == 33.3 and s["payoff"] == 2.0 and s["mdd_pct"] < 0 and not s["reliable"]


def test_backtest_runs_without_future_data():
    trades = backtest.run(_frame([100 * 1.004 ** i for i in range(300)]), target_rr=2.0)
    assert trades and all(t["result"] in ("목표", "손절", "기간만료") for t in trades)


def test_build_universe_merges_watchlist_and_top():
    u = build_universe({"005930": "삼성전자", "000990": "DB하이텍"}, {"005930": "삼성전자", "373220": "LG에너지솔루션"})
    assert list(u) == ["005930", "000990", "373220"]
    assert u["005930"] == {"name": "삼성전자", "source": "관심종목", "mcap_rank": 1}
    assert is_preferred("005935") and not is_preferred("005930")


def test_market_shock_is_plain_bool():
    assert market.regime(_frame([2500] * 99 + [2400]))["shock"] is True
