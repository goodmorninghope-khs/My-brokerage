"""자가학습부(채점): 지난 브리핑의 계획(plans.json)이 실제로 어떻게 됐는지 채점합니다.

승인하지 않은 계획도 '그대로 했다면'으로 채점합니다. 셋업별·의견별 적중률이 쌓이면
config의 규칙을 고칠 근거가 됩니다.  실행: python -m aisec.review
"""
import json
import sys
from datetime import date
from pathlib import Path

from . import data
from .backtest import COST_PCT, simulate_exit

ROOT = Path(__file__).resolve().parent.parent


def grade(plan: dict, bars, max_hold: int) -> dict:
    """계획 하나를 '그대로 했다면'으로 채점. bars 는 브리핑 날(진입일)부터의 일봉.

    브리핑은 장 시작 전에 나오므로 진입일은 브리핑 날짜 그 자체입니다(그 전날 종가로 만든 계획).
    결과와 함께 수익률(%)도 돌려줍니다. 수수료·세금은 백테스트와 같은 값을 뺍니다.
    """
    if not len(bars):
        return {"result": "진행중", "ret_pct": None}
    out = simulate_exit(bars, plan["entry"], plan["stop"], plan.get("targets") or [[plan["target"], 1.0]], max_hold)
    ret = None if out["exit"] is None else round((out["exit"] / plan["entry"] - 1) * 100 - COST_PCT, 2)
    return {"result": out["result"], "ret_pct": ret}


def first_signals(rows: list[dict]) -> list[dict]:
    """같은 종목·같은 셋업이 날마다 다시 뜨면 한 번의 신호로 봅니다(첫날만 남김).

    며칠째 이어진 같은 신호를 매번 세면 표본이 부풀어 '123건 전부 손절'처럼 실제보다 극단적으로 보입니다.
    직전 브리핑에 없다가 새로 뜬 신호만 독립 표본으로 셉니다.
    """
    days = sorted({r["day"] for r in rows})
    prev = {d: days[i - 1] if i else None for i, d in enumerate(days)}
    seen = {(r["day"], r["ticker"], r["setup"]) for r in rows}
    return [r for r in rows if (prev[r["day"]], r["ticker"], r["setup"]) not in seen]


def summarize(rows: list[dict]) -> dict:
    done = [r for r in rows if r["result"] != "진행중"]
    rets = [r["ret_pct"] for r in done if r["ret_pct"] is not None]
    return {"total": len(rows), "done": len(done), "open": len(rows) - len(done),
            "take_profit": sum("익절" in r["result"] for r in done),
            "stop": sum(r["result"] == "손절" for r in done),
            "expired": sum(r["result"] == "기간만료" for r in done),
            "avg_ret_pct": round(sum(rets) / len(rets), 2) if rets else None}


def line(label: str, s: dict) -> str:
    avg = "-" if s["avg_ret_pct"] is None else f"{s['avg_ret_pct']:+}%"
    return (f"| {label} | {s['total']} | {s['done']} | {s['take_profit']} | {s['stop']} | {s['expired']} | "
            f"{s['open']} | {avg} |")


def render(rows: list[dict]) -> str:
    uniq = first_signals(rows)
    head = ["| 구분 | 신호 | 끝난 것 | 익절(1차 이상) | 손절 | 기간만료 | 진행중 | 끝난 것 평균 수익 |", "|---|---|---|---|---|---|---|---|"]
    lines = ["# 자가학습 채점표", "",
             "계획을 '그대로 했다면'으로 채점합니다. 같은 종목·셋업이 며칠 연속 뜨면 **첫날 한 번만** 셉니다.",
             f"(중복 포함 {len(rows)}건 → 독립 신호 {len(uniq)}건)", "",
             "## 리스크 판정별 — 차단이 옳았나", *head,
             line("통과 → 승인 요청", summarize([r for r in uniq if r["verdict"].startswith("통과")])),
             line("차단 → 매매 보류", summarize([r for r in uniq if not r["verdict"].startswith("통과")])),
             line("전체", summarize(uniq)), "", "## 셋업별", *head]
    lines += [line(setup, summarize([r for r in uniq if r["setup"] == setup])) for setup in sorted({r["setup"] for r in uniq})]
    lines += ["", "## 독립 신호 목록", "", "| 날짜 | 종목 | 셋업 | 리스크 판정 | 결과 | 수익률 |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['date']} | {r['name']} | {r['setup']} | {r['verdict']} | {r['result']} | "
              f"{'-' if r['ret_pct'] is None else format(r['ret_pct'], '+') + '%'} |" for r in uniq]
    lines += ["", "표본이 20건 미만인 줄은 우연일 수 있습니다. 한 번의 급락 구간 결과로 규칙을 바꾸지 않습니다."]
    return "\n".join(lines)


def main(max_hold: int = 20) -> int:
    rows, cache = [], {}
    for plans_file in sorted((ROOT / "data").glob("*/plans.json")):
        day = plans_file.parent.name[:10]  # 장전 재계산 폴더(2026-10-12-0924)도 날짜만 씀
        for p in json.loads(plans_file.read_text(encoding="utf-8")):
            if p["ticker"] not in cache:
                first = min(f.parent.name[:10] for f in (ROOT / "data").glob("*/plans.json"))
                try:
                    cache[p["ticker"]] = data.ohlcv(p["ticker"], (data.today_kst() - date.fromisoformat(first)).days + 10, min_rows=1)
                except data.DataUnavailable as e:
                    print(f"건너뜀: {e}", file=sys.stderr)
                    cache[p["ticker"]] = None
            df = cache[p["ticker"]]
            if df is None:
                continue
            rows.append({"date": plans_file.parent.name, "day": day, "ticker": p["ticker"], "name": p["name"],
                         "setup": p["setup"], "verdict": p["verdict"], **grade(p, df[df.index >= day], max_hold)})
    if not rows:
        print("채점할 지난 계획이 없습니다.")
        return 0
    # 같은 날 장중 재계산 폴더가 여럿이면 그날 첫 폴더만 씁니다.
    first_folder = {}
    for r in rows:
        first_folder.setdefault(r["day"], r["date"])
    rows = [r for r in rows if r["date"] == first_folder[r["day"]]]
    out = ROOT / "reports" / "review.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(render(rows), encoding="utf-8")
    print(f"채점 완료 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
