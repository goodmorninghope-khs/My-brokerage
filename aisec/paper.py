"""모의투자 장부: 결재 카드에서 승인한 종목을 가상으로 사고, 규칙대로 팔았다고 치고 성적을 매깁니다.

    python -m aisec.paper approve 2026-10-12 005930   # 그날 브리핑의 계획대로 가상 매수 등록
    python -m aisec.paper update                      # 체결·청산 채점, reports/paper.md 갱신

체결 규칙 (원칙대로):
- 승인 다음 거래일 시가가 진입 구간 안이면 그 값에 체결. 벗어나면 '미체결' — 쫓아가 사지 않습니다.
- 체결 뒤에는 손절가·단계별 익절가·보유기간 규칙대로만 청산 (백테스트와 같은 함수).
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from . import data
from .backtest import COST_PCT, simulate_exit

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "paper" / "ledger.json"


def load() -> list[dict]:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []


def save(ledger: list[dict]) -> None:
    LEDGER.parent.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def approve(day: str, ticker: str, ledger: list[dict]) -> dict:
    plans_file = ROOT / "data" / day / "plans.json"
    if not plans_file.exists():
        raise SystemExit(f"{day} 브리핑 계획(plans.json)이 없습니다.")
    plans = [p for p in json.loads(plans_file.read_text(encoding="utf-8")) if p["ticker"] == ticker]
    if not plans:
        raise SystemExit(f"{day} 계획에 {ticker} 가 없습니다.")
    p = plans[0]
    if not p["verdict"].startswith("통과"):
        raise SystemExit(f"{p['name']}은(는) 리스크관리부 '차단' 종목이라 승인할 수 없습니다.")
    if any(x["ticker"] == ticker and x["status"] in ("대기", "보유") for x in ledger):
        raise SystemExit(f"{p['name']}은(는) 이미 장부에 진행 중인 거래가 있습니다.")
    pos = {"id": len(ledger) + 1, "approved_on": day, "ticker": ticker, "name": p["name"], "setup": p["setup"],
           "entry_zone": p["entry_zone"], "stop": p["stop"], "targets": p["targets"], "shares": p["shares"],
           "status": "대기"}
    ledger.append(pos)
    return pos


def settle(pos: dict, bars: pd.DataFrame, max_hold: int) -> dict:
    """승인일 이후 일봉으로 체결·청산을 판정. pos 를 갱신해 돌려줍니다."""
    after = bars[bars.index > pos["approved_on"]]
    if after.empty:
        return pos
    if pos["status"] == "대기":
        first_open = float(after["시가"].iloc[0])
        lo, hi = pos["entry_zone"]
        pos["fill_date"] = after.index[0].strftime("%Y-%m-%d")
        if not lo <= first_open <= hi:
            pos.update(status="미체결", result=f"시가 {first_open:,.0f}원이 진입 구간을 벗어남")
            return pos
        pos.update(status="보유", fill_price=first_open)
    held = after[after.index >= pos["fill_date"]]
    out = simulate_exit(held, pos["fill_price"], pos["stop"], pos["targets"], max_hold)
    if out["result"] == "진행중":
        last = float(held["종가"].iloc[-1])
        pos.update(last_close=last, pnl=round((last - pos["fill_price"]) * pos["shares"]))
        return pos
    pnl = (out["exit"] - pos["fill_price"]) * pos["shares"] - pos["fill_price"] * pos["shares"] * COST_PCT / 100
    pos.update(status="청산", result=out["result"], exit_date=out["exit_date"].strftime("%Y-%m-%d"),
               exit_price=round(float(out["exit"])), pnl=round(pnl))
    return pos


def scorecard(ledger: list[dict]) -> str:
    closed = [p for p in ledger if p["status"] == "청산"]
    holding = [p for p in ledger if p["status"] == "보유"]
    wins = [p for p in closed if p["pnl"] > 0]
    lines = ["# 모의투자 성적표", "",
             f"- 청산 {len(closed)}건 (익절 완료 {sum(p['result'] == '익절 완료' for p in closed)} · 손절 {sum('손절' in p['result'] for p in closed)}"
             f" · 기간만료 {sum('기간만료' in p['result'] for p in closed)}), 승률 {len(wins) / len(closed) * 100:.0f}%" if closed else "- 청산된 거래 없음",
             f"- 확정 손익 {sum(p['pnl'] for p in closed):+,}원 · 보유 {len(holding)}건 평가손익 {sum(p.get('pnl', 0) for p in holding):+,}원",
             f"- 미체결(쫓아 사지 않음) {sum(p['status'] == '미체결' for p in ledger)}건",
             f"- 20건까지 {max(0, 20 - len(closed))}건 남음 — 그 전의 승률은 우연일 수 있습니다.", "",
             "| # | 승인일 | 종목 | 셋업 | 상태 | 체결가 | 손절 | 익절 | 결과 | 손익(원) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for p in ledger:
        lines.append(f"| {p['id']} | {p['approved_on']} | {p['name']} | {p['setup']} | {p['status']} | "
                     f"{p.get('fill_price', '-')} | {p['stop']} | {' / '.join(str(t[0]) for t in p['targets'])} | {p.get('result', '-')} | {p.get('pnl', '-')} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ledger = load()
    if argv[:1] == ["approve"] and len(argv) == 3:
        pos = approve(argv[1], argv[2], ledger)
        save(ledger)
        print(f"가상 매수 등록: {pos['name']} {pos['shares']}주, 진입 구간 {pos['entry_zone']}, 손절 {pos['stop']}, 익절 {pos['targets']}")
        return 0
    if argv[:1] == ["update"]:
        max_hold = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["plan"]["max_hold_days"]
        for pos in ledger:
            if pos["status"] not in ("대기", "보유"):
                continue
            days = (date.today() - date.fromisoformat(pos["approved_on"])).days + 10
            try:
                settle(pos, data.ohlcv(pos["ticker"], days, min_rows=1), max_hold)
            except data.DataUnavailable as e:
                print(f"건너뜀: {e}", file=sys.stderr)
        save(ledger)
        out = ROOT / "reports" / "paper.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(scorecard(ledger), encoding="utf-8")
        print(f"장부 갱신 → {out}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
