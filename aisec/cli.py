"""아침 데이터 준비: python -m aisec.cli

관심종목 주가를 받아 지표를 계산하고, 필터 점수 상위 종목의 '자료 묶음(dossier)'을
data/<날짜>/ 에 씁니다. AI 6부서는 이 파일만 읽고 분석합니다(지어낸 숫자 금지).
"""
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from . import data, indicators, screen


def build_universe(watchlist: dict, top: dict) -> dict[str, dict]:
    """관심종목을 먼저, 시총 상위를 뒤에 합칩니다. 겹치면 관심종목으로 표시."""
    universe = {t: {"name": n, "source": "관심종목"} for t, n in watchlist.items()}
    for rank, (t, n) in enumerate(top.items(), 1):
        if t in universe:
            universe[t]["mcap_rank"] = rank
        else:
            universe[t] = {"name": n, "source": "시총상위", "mcap_rank": rank}
    return universe

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    out = ROOT / "data" / date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)

    snaps, failures, frames = {}, {}, {}
    top_cfg = cfg.get("market_cap_top") or {}
    top = {}
    if top_cfg.get("count"):
        try:
            top = data.market_cap_top(top_cfg["count"], top_cfg.get("market", "ALL"), top_cfg.get("exclude_preferred", True))
        except data.DataUnavailable as e:
            failures["market_cap_top"] = f"{e} → 관심종목만 분석합니다."
    universe = build_universe(cfg["watchlist"], top)

    for ticker, meta in universe.items():
        try:
            df = data.ohlcv(ticker, cfg["lookback_days"])
        except data.DataUnavailable as e:
            failures[ticker] = str(e)
            continue
        snap = indicators.snapshot(df)
        snap.update(meta)
        snap["foreign_net_5d"] = data.foreign_net_5d(ticker)
        snaps[ticker], frames[ticker] = snap, df

    if not snaps:
        print("데이터를 하나도 받지 못했습니다. 네트워크 허용 도메인(fchart.stock.naver.com, data.krx.co.kr)을 확인하세요.", file=sys.stderr)
        for t, msg in failures.items():
            print(f"  - {msg}", file=sys.stderr)
        return 2

    picks = screen.rank(snaps, cfg["screen"]["rules"], cfg["screen"]["top_n"])
    for p in picks:
        t = p["ticker"]
        recent = frames[t].tail(20)[["시가", "고가", "저가", "종가", "거래량"]]
        lines = [
            f"# 자료 묶음: {p['name']} ({t}) — 기준일 {p['date']}",
            "",
            "## 지표 스냅샷",
            "```json",
            json.dumps(p, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 펀더멘털 (KRX, 로그인 없으면 null)",
            "```json",
            json.dumps(data.fundamental(t), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 최근 20거래일 일봉",
            "```", recent.to_string(), "```",
        ]
        (out / f"dossier_{t}.md").write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "date": date.today().isoformat(),
        "risk": cfg["risk"],
        "picks": [{"ticker": p["ticker"], "name": p["name"], "score": p["score"], "passed": p["passed"]} for p in picks],
        "all": snaps,
        "failures": failures,
    }
    (out / "screen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: {len(snaps)}종목 수집, 실패 {len(failures)}, 회의 상정 {len(picks)}종목 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
