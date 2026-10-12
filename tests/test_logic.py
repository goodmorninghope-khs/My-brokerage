"""계산 로직 단위 테스트. 아래 숫자는 모두 테스트용 합성 데이터이며 실제 시세가 아닙니다."""
import pandas as pd

from aisec import backtest, indicators, market, plan, risk, setups
from aisec.cli import build_universe
from aisec.data import is_preferred

RISK = {"capital": 10_000_000, "risk_per_trade_pct": 1, "max_position_pct": 10, "max_total_exposure_pct": 60,
        "min_rr": 2.0, "stop_loss_pct": 5, "max_atr_pct": 5}


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


TP = [[10, 0.5], [15, 0.5]]


def test_plan_stop_capped_at_5pct_and_two_stage_targets():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9000, "why": ""}, 5, TP)   # 셋업 손절 -10% → -5%로
    assert p["stop"] == 9500 and p["targets"] == [[11000, 0.5], [11500, 0.5]] and p["rr"] == 2.0
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9700, "why": ""}, 5, TP)   # 셋업 손절이 더 가까우면 그쪽
    assert p["stop"] == 9700 and p["rr"] == 3.33


def test_risk_gate_sizes_by_one_percent_rule():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, 5, TP)
    g = risk.gate(p, atr_pct=2, market={"shock": False}, risk={**RISK, "max_position_pct": 20}, exposure=0)
    # 1% = 10만원, 주당 손실 500원 → 200주 = 200만원 = 비중 20% 한도와 정확히 일치
    assert g["shares"] == 200 and g["verdict"].startswith("통과")


def test_risk_gate_blocks_on_market_shock_and_high_volatility():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, 5, TP)
    g = risk.gate(p, atr_pct=6, market={"shock": True}, risk=RISK, exposure=0)
    failed = {c["name"] for c in g["checks"] if not c["pass"]}
    assert failed == {"05 변동성"} and g["verdict"].startswith("차단")


def test_risk_gate_blocks_total_exposure():
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, 5, TP)
    g = risk.gate(p, atr_pct=2, market={"shock": False}, risk=RISK, exposure=5_500_000)
    assert not g["checks"][1]["pass"]


def test_market_regime_and_shock():
    up = market.regime(_frame([2000 + 5 * i for i in range(100)]))
    assert up["regime"] == "상승장" and not up["shock"]
    crash = market.regime(_frame([2500] * 99 + [2400]))   # 하루 -4%
    assert crash["shock"]


def test_exit_counts_stop_first_when_both_hit():
    bars = _frame([100, 100], spread=0.2)   # 하루에 ±20% 흔들림 → 손절·목표 모두 닿음
    assert backtest.simulate_exit(bars, 100, 90, [[110, 1.0]], 20)["result"] == "손절"


def test_two_stage_take_profit_blends_exit():
    bars = _frame([100, 111, 116], spread=0.0)    # +11%에 1차, +16%에 2차
    out = backtest.simulate_exit(bars, 100, 95, [[110, 0.5], [115, 0.5]], 20)
    assert out["result"] == "익절 완료" and out["exit"] == (111 * 0.5 + 116 * 0.5)


def test_first_take_profit_then_stop():
    bars = _frame([100, 111, 90], spread=0.0)
    out = backtest.simulate_exit(bars, 100, 95, [[110, 0.5], [115, 0.5]], 20)
    assert out["result"] == "1차 익절 후 손절" and out["exit"] == 111 * 0.5 + 90 * 0.5


def test_stats_win_rate_and_drawdown():
    d1, d2, d3 = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    trades = [{"date": d1, "ret_pct": 10, "r": 2}, {"date": d2, "ret_pct": -5, "r": -1},
              {"date": d3, "ret_pct": -5, "r": -1}]
    s = backtest.stats(trades)
    assert s["win_rate_pct"] == 33.3 and s["payoff"] == 2.0 and s["mdd_pct"] < 0 and not s["reliable"]


def test_backtest_runs_without_future_data():
    trades = backtest.run(_frame([100 * 1.004 ** i for i in range(300)]), 5, TP)
    assert trades and all(t["result"] != "진행중" for t in trades)


def test_build_universe_merges_watchlist_and_top():
    u = build_universe({"005930": "삼성전자", "000990": "DB하이텍"}, {"005930": "삼성전자", "373220": "LG에너지솔루션"})
    assert list(u) == ["005930", "000990", "373220"]
    assert u["005930"] == {"name": "삼성전자", "source": "관심종목", "mcap_rank": 1}
    assert is_preferred("005935") and not is_preferred("005930")


def test_market_shock_is_plain_bool():
    assert market.regime(_frame([2500] * 99 + [2400]))["shock"] is True


def _pos(**kw):
    return {"id": 1, "approved_on": "2024-01-01", "ticker": "X", "name": "X", "setup": "돌파",
            "entry_zone": [99, 101], "stop": 95, "targets": [[110, 0.5], [115, 0.5]], "shares": 10, "status": "대기", **kw}


def test_paper_fills_in_zone_and_hits_target():
    from aisec.paper import settle
    bars = _frame([100, 100, 105, 112, 116], spread=0.0)   # 1/2 시가 100 체결 → 112에 1차, 116에 2차
    p = settle(_pos(), bars, 20)
    assert p["status"] == "청산" and p["result"] == "익절 완료" and p["fill_price"] == 100 and p["pnl"] > 0


def test_paper_does_not_chase_gap_up():
    from aisec.paper import settle
    p = settle(_pos(), _frame([100, 104, 106], spread=0.0), 20)   # 다음 날 시가 104 > 진입 상단 101
    assert p["status"] == "미체결"


def test_paper_holding_shows_unrealized_pnl():
    from aisec.paper import scorecard, settle
    p = settle(_pos(), _frame([100, 100, 102], spread=0.0), 20)
    assert p["status"] == "보유" and p["pnl"] == 20
    assert "보유 1건" in scorecard([p])


def test_holdings_trims_to_exposure_limit_closest_to_stop_first():
    from aisec.holdings import review
    pf = {"A": {"name": "A", "shares": 40, "avg_price": 124100}, "B": {"name": "B", "shares": 20, "avg_price": 210000}}
    res = review(pf, {"A": 120600, "B": 200500}, {**RISK, "max_total_exposure_pct": 70})
    a, b = res["rows"]
    assert a["action"] == "보유 유지" and b["sell"] == 10      # B가 손절선에 더 가까움
    assert b["stop"] == 199500 and b["targets"] == [[231000, 0.5], [241500, 0.5]]   # -5%, +10%/+15%
    assert res["after_pct"] <= 70


def test_holdings_stop_breach_sells_all_and_trend_broken_trimmed_first():
    from aisec.holdings import review
    pf = {"A": {"shares": 10, "avg_price": 100000}, "B": {"shares": 10, "avg_price": 100000}}
    res = review(pf, {"A": 94000, "B": 112000}, {**RISK, "stop_loss_pct": 5, "max_total_exposure_pct": 100})
    assert res["rows"][0]["action"] == "전량 매도"           # -6% → 손절선(-5%) 이탈
    assert res["rows"][1]["action"] == "5주 익절"            # +12% → 1차 익절가(+10%) 도달, 절반
    res = review(pf, {"A": 99000, "B": 99000}, {**RISK, "capital": 1_500_000, "max_total_exposure_pct": 70},
                 trends={"A": False, "B": True})
    assert res["rows"][1]["sell"] > 0 and res["rows"][0]["sell"] == 0   # 추세 이탈 B 먼저


def test_holdings_position_limit_is_soft_warning_only():
    from aisec.holdings import review
    pf = {"A": {"shares": 40, "avg_price": 124100}}
    res = review(pf, {"A": 120600}, {**RISK, "max_position_pct": 20, "max_total_exposure_pct": 70})
    r = res["rows"][0]
    assert r["sell"] == 0 and r["weight_pct"] > 20
    assert any("유연" in why for why in r["reasons"])


def test_near_breakout_and_pullback_signals():
    # 상승 추세에서 20일 고점 바로 아래(돌파 임박)
    closes = [100 * 1.003 ** i for i in range(200)] + [100 * 1.003 ** 199 * 0.99]
    found = {n["setup"]: n for n in setups.near(setups.features(_frame(closes, spread=0.002)).iloc[-1])}
    assert "돌파" in found and found["돌파"]["gap_pct"] > 0


def _ohlc(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    return pd.DataFrame(rows, columns=["시가", "고가", "저가", "종가"], index=idx).assign(거래량=1000)


def test_candle_hammer_after_decline():
    from aisec.candles import patterns
    rows = [(110 - i, 111 - i, 109 - i, 110 - i) for i in range(8)]   # 하락
    rows.append((101.5, 102, 95, 102))                                # 긴 아래꼬리, 윗꼬리 거의 없음
    assert patterns(_ohlc(rows))["망치형"].iloc[-1]


def test_candle_hammer_ignored_without_prior_decline():
    from aisec.candles import patterns
    rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(8)]   # 상승
    rows.append((108.5, 109, 102, 109))
    assert not patterns(_ohlc(rows))["망치형"].iloc[-1]


def test_candle_bullish_and_bearish_engulfing():
    from aisec.candles import patterns
    down = [(110 - i, 111 - i, 109 - i, 110 - i) for i in range(7)] + [(104, 104.5, 101, 101.5), (101, 106, 100.5, 105)]
    assert patterns(_ohlc(down))["상승장악형"].iloc[-1]
    up = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(7)] + [(106, 109.5, 105.5, 109), (109.5, 110, 104, 105)]
    assert patterns(_ohlc(up))["하락장악형"].iloc[-1]


def test_candle_backtest_runs():
    trades = backtest.run_candles(_frame([100 * (1.01 if i % 7 else 0.95) ** (i % 10) for i in range(300)]), 5, TP)
    assert all(t["result"] != "진행중" for t in trades)


def test_holdings_single_share_takes_profit_at_first_target():
    from aisec.holdings import review
    res = review({"A": {"shares": 1, "avg_price": 100000}}, {"A": 111000}, {**RISK, "max_total_exposure_pct": 100})
    assert res["rows"][0]["sell"] == 1


def test_risk_gate_blocks_during_pause():
    import datetime
    p = plan.make_plan(10000, {"setup": "돌파", "stop": 9500, "why": ""}, 5, TP)
    far = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    g = risk.gate(p, atr_pct=2, market={"shock": False}, risk={**RISK, "pause_until": far}, exposure=0)
    assert g["verdict"].startswith("차단") and g["checks"][-1]["name"] == "06 휴식"
    g = risk.gate(p, atr_pct=2, market={"shock": False}, risk={**RISK, "pause_until": "2000-01-01"}, exposure=0)
    assert g["verdict"].startswith("통과")
