"""계산 로직 단위 테스트. 아래 숫자는 모두 테스트용 합성 데이터이며 실제 시세가 아닙니다."""
import sys

import pandas as pd

from aisec import backtest, brief, holdings, indicators, market, plan, review, risk, setups, strength
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


def test_regime_series_matches_today_regime_and_uses_past_only():
    up = _frame([100 + i for i in range(80)])
    ser = market.regime_series(up)
    assert ser.iloc[-1] == market.regime(up)["regime"] == "상승장"
    assert ser.iloc[:59].isna().all()  # 60일선이 생기기 전에는 판정하지 않음
    # 뒤에 급락을 붙여도 앞날의 판정은 바뀌지 않음(미래 데이터 금지)
    longer = _frame([100 + i for i in range(80)] + [50] * 30)
    assert market.regime_series(longer).iloc[:80].fillna("-").equals(ser.fillna("-"))


def test_backtest_by_regime_tags_trades_with_signal_day_regime():
    idx = pd.date_range("2024-01-01", periods=4, freq="B")
    regimes = pd.Series(["상승장", "상승장", "하락장", "하락장"], index=idx)
    trades = [{"setup": "돌파", "date": idx[1], "ret_pct": 10, "r": 2},
              {"setup": "돌파", "date": idx[3], "ret_pct": -5, "r": -1}]
    out = backtest.by_setup_regime(trades, regimes)
    assert out["돌파"]["상승장"]["trades"] == 1 and out["돌파"]["하락장"]["win_rate_pct"] == 0.0
    assert backtest.by_setup_regime(trades, None) == {}


def test_rs_rating_ranks_stronger_stock_higher_vs_index():
    n = 300
    idx_close = pd.Series([100 * 1.001 ** i for i in range(n)])
    closes = {"강": pd.Series([100 * 1.003 ** i for i in range(n)]),
              "보통": pd.Series([100 * 1.001 ** i for i in range(n)]),
              "약": pd.Series([100 * 0.999 ** i for i in range(n)])}
    rs = strength.rs_ratings(closes, idx_close)
    assert rs["강"]["rs_rating"] == 99 and rs["약"]["rs_rating"] == 1
    assert rs["강"]["rs_raw"] > 0 > rs["약"]["rs_raw"] and rs["강"]["rs_20d"] > 0


def test_trend_template_full_marks_in_steady_uptrend():
    tt = strength.trend_template(_frame([100 * 1.004 ** i for i in range(300)]), rs_rating=90)
    assert tt["passed"] == tt["total"] == 7
    down = strength.trend_template(_frame([100 * 0.996 ** i for i in range(300)]), rs_rating=10)
    assert down["passed"] <= 1


def test_score_prefers_regime_tested_setup_and_penalizes_warning():
    good = {"trades": 30, "avg_ret_pct": 1.0, "reliable": True}
    bad = {"trades": 30, "avg_ret_pct": -0.5, "reliable": True}
    tt = {"passed": 7, "total": 7}
    rs = {"rs_rating": 99}
    a = strength.score(good, good, rs, tt, None, False, False)
    b = strength.score(bad, good, rs, tt, None, False, False)
    c = strength.score(good, good, rs, tt, None, False, True)
    assert a["total"] == 75 and b["total"] == 45 and c["total"] == 55
    assert strength.score(None, None, None, None, None, False, True)["total"] == 0  # 0점 아래로 내려가지 않음


def test_plan_reports_stop_as_multiple_of_atr():
    p = plan.make_plan(10000, {"setup": "눌림목", "stop": 9000, "why": "x"}, 5, TP, atr=400)
    assert p["stop"] == 9500 and p["stop_atr_multiple"] == 1.25
    assert plan.make_plan(10000, {"setup": "눌림목", "stop": 9000, "why": "x"}, 5, TP)["stop_atr_multiple"] is None


def test_cli_end_to_end_with_synthetic_data(tmp_path, monkeypatch):
    """네트워크 없이 아침 계산 전체(스크리닝→점수→리스크→결재 자료)가 끝까지 도는지 확인. 시세는 합성 데이터."""
    import json
    from datetime import date

    import yaml

    from aisec import cli, data

    cfg = yaml.safe_load(open(cli.ROOT / "config.yaml", encoding="utf-8"))
    cfg.update({"watchlist": {"000001": "상승주", "000002": "하락주", "000003": "보유주"},
                "market_cap_top": {"count": 0}, "lookback_days": 600,
                "portfolio": {"000003": {"name": "보유주", "shares": 10, "avg_price": 100}}})
    cfg["risk"]["pause_until"] = None
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    frames = {"000001": _frame([100 * 1.003 ** i for i in range(400)]),
              "000002": _frame([100 * 0.998 ** i for i in range(400)]),
              "000003": _frame([100 + (i % 30) for i in range(400)])}
    monkeypatch.setattr(data, "ohlcv", lambda t, n: frames[t])
    monkeypatch.setattr(data, "kospi", lambda n=200: _frame([2500 * 1.001 ** i for i in range(400)]))
    monkeypatch.setattr(data, "foreign_net_5d", lambda t: None)
    monkeypatch.setattr(data, "fundamental", lambda t: None)
    monkeypatch.setattr(data, "today_kst", lambda: date(2025, 7, 1))
    monkeypatch.setattr(sys, "argv", ["cli"])

    assert cli.main() == 0
    out = tmp_path / "data" / "2025-07-01"
    screen = json.loads((out / "screen.json").read_text(encoding="utf-8"))
    u = screen["universe"]
    assert u["000001"]["rs_rating"] > u["000002"]["rs_rating"]
    assert "backtest_by_setup_regime" in screen
    assert "상대강도" in (out / "holdings.md").read_text(encoding="utf-8")
    assert screen["picks"] and "score" in screen["picks"][0]
    assert all(c["ticker"] != "000003" for c in screen["picks"] + screen["other_candidates"])  # 보유 종목은 후보에서 제외
    assert "강도 점수" in (out / f"dossier_{screen['picks'][0]['ticker']}.md").read_text(encoding="utf-8")
    # 결재 문서 초안과 보유 종목 자료 묶음도 코드가 만든다
    draft = (out / "brief_draft.md").read_text(encoding="utf-8")
    assert "## 오늘 결재할 것" in draft and "[[" in draft and "급변 규칙" in draft
    assert screen["market"]["shock_outlook"][0]["upper"] > screen["market"]["kospi"]
    assert "보유 종목" in (out / "dossier_000003.md").read_text(encoding="utf-8")
    (tmp_path / "draft.md").write_text(draft, encoding="utf-8")
    assert brief.main(["check", str(tmp_path / "draft.md")]) == 1  # 안 채운 칸이 남으면 실패
    (tmp_path / "done.md").write_text("# 다 채움", encoding="utf-8")
    assert brief.main(["check", str(tmp_path / "done.md")]) == 0


def test_shock_outlook_matches_shock_rule():
    """급변 해제 구간은 regime()의 급변 판정과 같은 상수로 계산한다: 구간 안이면 꺼지고, 밖이면 켜진다."""
    df = _frame([100.0] * 70)
    o = market.shock_outlook(df)[0]
    assert o["possible"] and (o["lower"], o["upper"]) == (97.0, 103.0)
    inside = _frame([100.0] * 70 + [102.9])
    outside = _frame([100.0] * 70 + [96.9])
    assert market.regime(inside)["shock"] is False and market.regime(outside)["shock"] is True
    # 5일 전보다 이미 많이 빠져 있으면 하루 구간과 겹치지 않는다 → 내일 해제 불가
    crash = _frame([100.0] * 66 + [100, 100, 100, 100, 88])
    c = market.shock_outlook(crash)[0]
    assert c["possible"] is False and c["floor_5d"] == 93.0


def test_fit_setups_sorts_by_regime_numbers():
    st = {"눌림목": {"하락장": {"trades": 148, "win_rate_pct": 38.5, "avg_ret_pct": 0.02, "reliable": True}},
          "모멘텀": {"하락장": {"trades": 134, "win_rate_pct": 29.9, "avg_ret_pct": -1.05, "reliable": True},
                  "상승장": {"trades": 300, "win_rate_pct": 40.0, "avg_ret_pct": 1.09, "reliable": True}},
          "반전": {"하락장": {"trades": 5, "win_rate_pct": 20.0, "avg_ret_pct": 3.0, "reliable": False}}}
    f = market.fit_setups(st, "하락장")
    assert f["fit"] == [] and [r["setup"] for r in f["thin"]] == ["눌림목"]
    assert [r["setup"] for r in f["avoid"]] == ["모멘텀"] and [r["setup"] for r in f["unknown"]] == ["반전"]
    assert [r["setup"] for r in market.fit_setups(st, "상승장")["fit"]] == ["모멘텀"]
    assert "반전" not in market.STANCE["하락장"]  # 태세 문구가 특정 셋업을 못 박지 않는다


def test_score_scales_with_regime_edge():
    tt, rs = {"passed": 7, "total": 7}, {"rs_rating": 99}
    thin = {"trades": 148, "avg_ret_pct": 0.02, "reliable": True}
    half = {"trades": 148, "avg_ret_pct": 0.5, "reliable": True}
    assert strength.score(thin, thin, rs, tt, None, False, False)["parts"]["국면별 셋업 성적"] == 1
    assert strength.score(half, half, rs, tt, None, False, False)["parts"]["국면별 셋업 성적"] == 15


def test_holdings_warns_when_low_touches_stop_but_keeps_rule_action():
    pf = {"A": {"name": "가", "shares": 50, "avg_price": 22650}}
    risk_cfg = {**RISK, "max_position_pct": 20, "max_total_exposure_pct": 70}
    closes = [22000.0] * 70 + [21950.0]
    df = _frame(closes)
    df.iloc[-1, df.columns.get_loc("저가")] = 21450.0
    d = holdings.bar_detail(df, 21518)
    assert d["low"] == 21450 and d["days_below_stop"] == 0
    res = holdings.review(pf, {"A": 21950}, risk_cfg, details={"A": d})
    row = res["rows"][0]
    assert row["action"] == "보유 유지" and any("장중 저가" in w for w in row["warnings"])  # 제안은 그대로, 경고만
    # 손절선 아래 마감이 이어지고 손실이 한도를 넘으면 둘 다 경고
    df2 = _frame([22000.0] * 70 + [20750.0, 20350.0, 20050.0])
    d2 = holdings.bar_detail(df2, 21518)
    res2 = holdings.review(pf, {"A": 20050}, risk_cfg, details={"A": d2})
    w = res2["rows"][0]["warnings"]
    assert d2["days_below_stop"] == 3 and res2["rows"][0]["action"] == "전량 매도"
    assert any("3거래일째" in x for x in w) and any("한도 1% 초과" in x for x in w)
    assert "⚠" in holdings.render(res2, "테스트")


def test_excess_20d_history_turns_negative_when_stock_lags():
    idx = _frame([100 * 1.01 ** i for i in range(60)])["종가"]
    lag = _frame([100.0] * 60)["종가"]
    h = strength.excess_20d_history(lag, idx, 3)
    assert len(h) == 3 and all(x["rs_20d"] < 0 for x in h)


def test_review_counts_repeated_signal_once_and_grades_from_brief_day():
    rows = [{"day": d, "date": d, "ticker": "A", "name": "가", "setup": "눌림목", "verdict": "차단 → 매매 보류", "result": "손절", "ret_pct": -5.25}
            for d in ("2026-10-12", "2026-10-13", "2026-10-14")]
    rows.append({"day": "2026-10-16", "date": "2026-10-16", "ticker": "A", "name": "가", "setup": "눌림목", "verdict": "통과 → 승인 요청",
                 "result": "진행중", "ret_pct": None})  # 10/15 에는 없다가 다시 뜬 신호는 새 표본
    rows.append({"day": "2026-10-15", "date": "2026-10-15", "ticker": "B", "name": "나", "setup": "돌파", "verdict": "차단 → 매매 보류",
                 "result": "1차 익절 후 손절", "ret_pct": 2.0})
    uniq = review.first_signals(rows)
    assert [(r["day"], r["ticker"]) for r in uniq] == [("2026-10-12", "A"), ("2026-10-16", "A"), ("2026-10-15", "B")]
    s = review.summarize(uniq)
    assert (s["total"], s["done"], s["take_profit"], s["stop"], s["open"]) == (3, 2, 1, 1, 1)
    assert "독립 신호 3건" in review.render(rows)
    # 진입일은 브리핑 날짜 그 자체: 첫 봉에서 손절가를 건드리면 손절
    bars = _frame([100.0, 100.0])
    bars.iloc[0, bars.columns.get_loc("저가")] = 94.0
    g = review.grade({"entry": 100, "stop": 95, "targets": [[110, 0.5], [115, 0.5]]}, bars, 20)
    assert g["result"] == "손절" and g["ret_pct"] == -5.25
    assert review.grade({"entry": 100, "stop": 95, "target": 110}, bars.iloc[:0], 20)["result"] == "진행중"
