"""아침 데이터 준비: python -m aisec.cli

스크리닝 → 셋업 판별 → 운용 계획 → 리스크 5기준 → 백테스트까지 '숫자'를 모두 계산해
data/<날짜>/ 에 씁니다. AI 부서들은 이 파일만 읽고 해석합니다(지어낸 숫자 금지).
"""
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from . import backtest, data, indicators, market, plan, risk, setups

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
    bt = c["backtest_ticker"]
    passed = c["risk"]["verdict"].startswith("통과")
    return (passed, bt.get("trades", 0) >= 5 and bt.get("avg_ret_pct", -99) > 0, bt.get("avg_ret_pct", -99))


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    out = ROOT / "data" / date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    failures = {}

    # 마켓부(숫자): 시장 국면
    try:
        mkt = market.regime(data.kospi())
    except data.DataUnavailable as e:
        mkt = {"regime": "판정불가", "stance": "자료 없음 — 보수적으로", "shock": True}
        failures["kospi"] = f"{e} → 시장 급변으로 간주해 신규 매수를 차단합니다."

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

    rr, hold, rcfg = cfg["plan"]["target_rr"], cfg["plan"]["max_hold_days"], cfg["risk"]
    held = exposure(cfg.get("portfolio"), {t: s["close"] for t, s in snaps.items()})

    # 백테스트: 전 종목 3년치 → 셋업별 성적 (전체 합산 + 종목별)
    all_trades, ticker_trades = [], {}
    for t, df in frames.items():
        ticker_trades[t] = backtest.run(df, rr, hold)
        all_trades += ticker_trades[t]
    pooled = backtest.by_setup(all_trades, rcfg["risk_per_trade_pct"])

    # 오늘 셋업 → 운용 계획 → 리스크 5기준
    candidates = []
    for t, df in frames.items():
        f = setups.features(df)
        last = f.iloc[-1]
        atr_pct = float(last["atr"] / last["close"] * 100)
        for s in setups.detect(last):
            p = plan.make_plan(float(last["close"]), s, rr)
            g = risk.gate(p, atr_pct, mkt, rcfg, held)
            mine = [x for x in ticker_trades[t] if x["setup"] == s["setup"]]
            candidates.append({
                "ticker": t, "name": snaps[t]["name"], "plan": p, "risk": g, "atr_pct": round(atr_pct, 2),
                "backtest_ticker": backtest.stats(mine, rcfg["risk_per_trade_pct"]),
                "backtest_all": pooled.get(s["setup"], {"trades": 0}),
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
            "", "## 수급·펀더멘털 (KRX 로그인 없으면 null)", "```json",
            json.dumps({"foreign_net_5d": data.foreign_net_5d(t), "fundamental": data.fundamental(t)}, ensure_ascii=False, indent=2),
            "```", "", "## 최근 20거래일 일봉", "```", recent.to_string(), "```",
        ]
        (out / f"dossier_{t}.md").write_text("\n".join(doc), encoding="utf-8")

    plans = [{"ticker": c["ticker"], "name": c["name"], "verdict": c["risk"]["verdict"], **c["plan"]} for c in candidates]
    (out / "plans.json").write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "date": date.today().isoformat(), "market": mkt, "risk_rules": rcfg,
        "picks": [{"ticker": c["ticker"], "name": c["name"], "setup": c["plan"]["setup"], "verdict": c["risk"]["verdict"]} for c in picks],
        "other_candidates": [{"ticker": c["ticker"], "name": c["name"], "setup": c["plan"]["setup"], "verdict": c["risk"]["verdict"]} for c in candidates if c not in picks],
        "backtest_by_setup": pooled, "universe": snaps, "failures": failures,
    }
    (out / "screen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"완료: {len(frames)}종목 수집, 셋업 성립 {len(candidates)}건, 회의 상정 {len(picks)}건, 시장 {mkt['regime']} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
