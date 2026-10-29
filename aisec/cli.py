"""아침 데이터 준비: python -m aisec.cli

스크리닝 → 셋업 판별 → 운용 계획 → 리스크 5기준 → 백테스트까지 '숫자'를 모두 계산해
data/<날짜>/ 에 씁니다. AI 부서들은 이 파일만 읽고 해석합니다(지어낸 숫자 금지).
"""
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from . import backtest, candles, data, holdings, indicators, market, plan, risk, setups, strength

ROOT = Path(__file__).resolve().parent.parent


def build_universe(watchlist: dict, top: dict) -> dict[str, dict]:
    """관심종목을 먼저, 시총 상위를 뒤에 합칩니다. 겹치면 관심종목으로 표시."""
    universe = {t: {"name": n, "source": "관심종목"} for t, n in watchlist.items()}
    for rank, (t, n) in enumerate(top.items(), 1):
        if t in universe:
            universe[t]["mcap_rank"] = rank
        else:
            universe[t] = {"name": n, "source": "시총상위", "mcap_rank": rank}
    return universe


def exposure(portfolio: dict, last_close: dict) -> float:
    return sum(h["shares"] * last_close.get(t, h["avg_price"]) for t, h in (portfolio or {}).items())


def rank_key(c: dict) -> tuple:
    """순위: 리스크 통과 > 종합 점수(국면별 셋업 성적·상대강도·추세 템플릿·종목 백테스트·캔들) > 종목 평균 수익."""
    bt = c["backtest_ticker"]
    passed = c["risk"]["verdict"].startswith("통과")
    return (passed, c.get("score", {}).get("total", 0), bt.get("avg_ret_pct", -99))


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    # 장중 재계산은 python -m aisec.cli 1430 처럼 꼬리표를 붙여 아침 기록을 덮어쓰지 않게 합니다.
    tag = f"-{sys.argv[1]}" if len(sys.argv) > 1 else ""
    out = ROOT / "data" / f"{data.today_kst().isoformat()}{tag}"
    out.mkdir(parents=True, exist_ok=True)
    failures = {}

    # 마켓부(숫자): 시장 국면. 지수도 3년치를 받아 백테스트 거래마다 '그날의 국면'을 붙입니다.
    kospi_df, regimes = None, None
    try:
        kospi_df = data.kospi(cfg["lookback_days"])
        mkt = market.regime(kospi_df)
        regimes = market.regime_series(kospi_df)
    except data.DataUnavailable as e:
        mkt = {"regime": "판정불가", "stance": "자료 없음 — 보수적으로", "shock": True}
        failures["kospi"] = f"{e} → 시장 급변으로 간주해 신규 매수를 차단합니다. 상대강도는 코스피 대비가 아닌 절대 수익률로 계산합니다."

    # 스크리닝부: 대상 종목
    top_cfg = cfg.get("market_cap_top") or {}
    top = {}
    if top_cfg.get("count"):
        try:
            top = data.market_cap_top(top_cfg["count"], top_cfg.get("market", "ALL"), top_cfg.get("exclude_preferred", True))
        except data.DataUnavailable as e:
            failures["market_cap_top"] = f"{e} → 관심종목만 분석합니다."
    universe = build_universe(cfg["watchlist"], top)

    frames, snaps = {}, {}
    for ticker, meta in universe.items():
        try:
            frames[ticker] = data.ohlcv(ticker, cfg["lookback_days"])
        except data.DataUnavailable as e:
            failures[ticker] = str(e)
            continue
        snaps[ticker] = {**meta, **indicators.snapshot(frames[ticker])}

    if not frames:
        print("데이터를 하나도 받지 못했습니다. 네트워크 허용 도메인(fchart.stock.naver.com, data.krx.co.kr)을 확인하세요.", file=sys.stderr)
        for msg in failures.values():
            print(f"  - {msg}", file=sys.stderr)
        return 2

    tp, hold, rcfg = cfg["plan"]["take_profit"], cfg["plan"]["max_hold_days"], cfg["risk"]
    sl = rcfg["stop_loss_pct"]
    held = exposure(cfg.get("portfolio"), {t: s["close"] for t, s in snaps.items()})

    # 백테스트: 전 종목 3년치 → 셋업별 성적 (전체 합산 + 종목별)
    all_trades, ticker_trades = [], {}
    for t, df in frames.items():
        ticker_trades[t] = backtest.run(df, sl, tp, hold)
        all_trades += ticker_trades[t]
    pooled = backtest.by_setup(all_trades, rcfg["risk_per_trade_pct"])
    pooled_regime = backtest.by_setup_regime(all_trades, regimes, rcfg["risk_per_trade_pct"])

    # 상대강도(RS 등급)와 추세 템플릿: 코스피 대비 얼마나 강한가, 추세가 살아 있는가
    rs = strength.rs_ratings({t: df["종가"] for t, df in frames.items()},
                             kospi_df["종가"] if kospi_df is not None else None)
    templates = {t: strength.trend_template(df, rs.get(t, {}).get("rs_rating")) for t, df in frames.items()}
    for t in snaps:
        snaps[t].update({**rs.get(t, {}), "trend_template": f"{templates[t]['passed']}/{templates[t]['total']}"})

    # 캔들 백테스트: 상승 반전 캔들이 우리 종목에서 실제로 통했나? 통한 것만 '검증된 캔들'
    candle_trades = []
    for df in frames.values():
        candle_trades += backtest.run_candles(df, sl, tp, hold)
    candle_stats = backtest.by_setup(candle_trades, rcfg["risk_per_trade_pct"])
    validated = {n for n, st in candle_stats.items() if st.get("reliable") and st.get("avg_ret_pct", -1) > 0}
    recent_candles = {t: candles.recent(df, 3) for t, df in frames.items()}

    def candle_flags(t: str) -> tuple[bool, bool]:
        rc, last_day = recent_candles[t], snaps[t]["date"]
        bonus = any(x["date"] == last_day and x["pattern"] in validated for x in rc)
        warning = any(x["kind"] == "하락" for x in rc[:2])
        return bonus, warning

    # 오늘 셋업 → 운용 계획 → 리스크 5기준
    candidates, waiting = [], []
    for t, df in frames.items():
        f = setups.features(df)
        last = f.iloc[-1]
        if t not in (cfg.get("portfolio") or {}):
            for n in setups.near(last):
                waiting.append({"ticker": t, "name": snaps[t]["name"], "close": round(float(last["close"])), **n,
                                "backtest_all": pooled.get(n["setup"], {"trades": 0})})
            # 캔들 신호만 있고 셋업은 없는 종목: 다음 날 신호 캔들 고가를 넘으면 '확인'
            for x in recent_candles[t]:
                if x["date"] == snaps[t]["date"] and x["pattern"] in validated:
                    hi = round(float(df["고가"].iloc[-1]))
                    waiting.append({"ticker": t, "name": snaps[t]["name"], "close": round(float(last["close"])),
                                    "setup": f"캔들:{x['pattern']}", "trigger": hi,
                                    "gap_pct": round((hi / float(last["close"]) - 1) * 100, 2),
                                    "condition": f"{x['meaning']}. 다음 날 이 고가를 넘으면 신호 확인",
                                    "backtest_all": candle_stats.get(x["pattern"], {"trades": 0})})
        atr_pct = float(last["atr"] / last["close"] * 100)
        for s in setups.detect(last):
            p = plan.make_plan(float(last["close"]), s, sl, tp, float(last["atr"]))
            g = risk.gate(p, atr_pct, mkt, rcfg, held)
            mine = [x for x in ticker_trades[t] if x["setup"] == s["setup"]]
            bonus, warning = candle_flags(t)
            reg_stats = pooled_regime.get(s["setup"], {}).get(mkt["regime"])
            ticker_stats = backtest.stats(mine, rcfg["risk_per_trade_pct"])
            candidates.append({
                "ticker": t, "name": snaps[t]["name"], "plan": p, "risk": g, "atr_pct": round(atr_pct, 2),
                "candles": recent_candles[t], "candle_bonus": bonus, "candle_warning": warning,
                "backtest_ticker": ticker_stats,
                "backtest_all": pooled.get(s["setup"], {"trades": 0}),
                "backtest_regime": {"regime": mkt["regime"], **(reg_stats or {"trades": 0})},
                "rs": rs.get(t), "trend_template": templates[t],
                "score": strength.score(reg_stats, pooled.get(s["setup"]), rs.get(t), templates[t],
                                        ticker_stats, bonus, warning),
            })
    candidates.sort(key=rank_key, reverse=True)
    # 한 종목에 셋업이 여러 개 성립하면 가장 좋은 셋업 하나만 회의에 올립니다.
    picks, seen = [], set()
    for c in candidates:
        if c["ticker"] not in seen and len(picks) < cfg["screen"]["top_n"]:
            picks.append(c)
            seen.add(c["ticker"])

    for c in picks:
        t = c["ticker"]
        recent = frames[t].tail(20)[["시가", "고가", "저가", "종가", "거래량"]]
        doc = [
            f"# 자료 묶음: {c['name']} ({t}) — 셋업 [{c['plan']['setup']}]",
            "", "## 지표 스냅샷", "```json", json.dumps(snaps[t], ensure_ascii=False, indent=2), "```",
            "", "## 운용부 계획", "```json", json.dumps(c["plan"], ensure_ascii=False, indent=2), "```",
            "", "## 리스크관리부 5기준", "```json", json.dumps(c["risk"], ensure_ascii=False, indent=2), "```",
            "", "## 백테스트 (이 종목·이 셋업, 최근 3년)", "```json", json.dumps(c["backtest_ticker"], ensure_ascii=False, indent=2), "```",
            "", "## 백테스트 (전 종목 합산·이 셋업)", "```json", json.dumps(c["backtest_all"], ensure_ascii=False, indent=2), "```",
            "", f"## 백테스트 (전 종목·이 셋업·지금과 같은 국면: {c['backtest_regime']['regime']})", "```json",
            json.dumps(c["backtest_regime"], ensure_ascii=False, indent=2), "```",
            "", "## 강도 점수 (상대강도 · 추세 템플릿 · 종합 점수표)", "```json",
            json.dumps({"상대강도": c["rs"], "추세_템플릿": c["trend_template"], "종합_점수": c["score"]},
                       ensure_ascii=False, indent=2), "```",
            "", "## 캔들 신호 (최근 3거래일, 최신 먼저)", "```json",
            json.dumps({"recent": c["candles"], "검증된_상승캔들_가점": c["candle_bonus"], "하락캔들_경고": c["candle_warning"]},
                       ensure_ascii=False, indent=2), "```",
            "", "## 수급·펀더멘털 (KRX 로그인 없으면 null)", "```json",
            json.dumps({"foreign_net_5d": data.foreign_net_5d(t), "fundamental": data.fundamental(t)}, ensure_ascii=False, indent=2),
            "```", "", "## 최근 20거래일 일봉", "```", recent.to_string(), "```",
        ]
        (out / f"dossier_{t}.md").write_text("\n".join(doc), encoding="utf-8")

    # 보유 종목 점검: 실제 종가와 추세(20일선<60일선, 종가<60일선)로 원칙 제안
    pf = cfg.get("portfolio") or {}
    if pf and all(t in frames for t in pf):
        trends = {}
        for t in pf:
            last = setups.features(frames[t]).iloc[-1]
            trends[t] = bool(last["ma20"] < last["ma60"] and last["close"] < last["ma60"])
        prices = {t: round(float(frames[t]["종가"].iloc[-1])) for t in pf}
        hres = holdings.review(pf, prices, rcfg, trends, tp)
        text = holdings.render(hres, f"{snaps[next(iter(pf))]['date']} 종가")
        text += "\n\n### 보유 종목 강도 (코스피 대비)\n" + "\n".join(
            f"- {pf[t].get('name', t)}: 상대강도 {rs.get(t, {}).get('rs_rating', '-')}/99 · 최근 20일 코스피 대비 "
            f"{rs.get(t, {}).get('rs_20d', '-')}%p · 추세 템플릿 {templates[t]['passed']}/{templates[t]['total']}" for t in pf)
        notes = [f"- 캔들 {pf[t].get('name', t)}: " + ", ".join(f"{x['date']} {x['pattern']}({x['kind']})" for x in recent_candles[t])
                 for t in pf if recent_candles[t]]
        if notes:
            text += "\n\n### 보유 종목 캔들 신호 (최근 3거래일)\n" + "\n".join(notes)
        (out / "holdings.md").write_text(text, encoding="utf-8")
    elif pf:
        failures["holdings"] = f"보유 종목 시세 일부를 받지 못해 점검을 건너뜁니다: {[t for t in pf if t not in frames]}"

    plans = [{"ticker": c["ticker"], "name": c["name"], "verdict": c["risk"]["verdict"], "shares": c["risk"]["shares"], **c["plan"]} for c in candidates]
    (out / "plans.json").write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "date": data.today_kst().isoformat(), "market": mkt, "risk_rules": rcfg,
        "picks": [{"ticker": c["ticker"], "name": c["name"], "setup": c["plan"]["setup"], "verdict": c["risk"]["verdict"],
                   "score": c["score"]["total"], "rs_rating": (c["rs"] or {}).get("rs_rating"),
                   "trend_template": f"{c['trend_template']['passed']}/{c['trend_template']['total']}"} for c in picks],
        "other_candidates": [{"ticker": c["ticker"], "name": c["name"], "setup": c["plan"]["setup"], "verdict": c["risk"]["verdict"]} for c in candidates if c not in picks],
        # 오늘의 원칙 추천: 리스크 통과 후보 중 1순위 (하루 신규 매수 max_daily_new_buys 건)
        "recommend": [{"ticker": c["ticker"], "name": c["name"], "setup": c["plan"]["setup"]}
                      for c in picks if c["risk"]["verdict"].startswith("통과")][: rcfg.get("max_daily_new_buys", 1)],
        # 대기 목록: 신호 가격까지 가까운 순
        "waiting": sorted(waiting, key=lambda w: abs(w["gap_pct"]))[:5],
        "backtest_by_setup": pooled,
        "backtest_by_setup_regime": pooled_regime,
        "candle_backtest": candle_stats, "validated_candles": sorted(validated),
        "candles_today": {t: rc for t, rc in recent_candles.items() if rc and rc[0]["date"] == snaps[t]["date"]}, "universe": snaps, "failures": failures,
    }
    (out / "screen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"완료: {len(frames)}종목 수집, 셋업 성립 {len(candidates)}건, 회의 상정 {len(picks)}건, 시장 {mkt['regime']} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
