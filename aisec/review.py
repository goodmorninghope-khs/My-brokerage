"""자가학습부(채점): 지난 브리핑의 계획(plans.json)이 실제로 어떻게 됐는지 채점합니다.

승인하지 않은 계획도 '그대로 했다면'으로 채점합니다. 셋업별·의견별 적중률이 쌓이면
config의 규칙을 고칠 근거가 됩니다.  실행: python -m aisec.review
"""
import json
import sys
from datetime import date
from pathlib import Path

from . import data
from .backtest import simulate_exit

ROOT = Path(__file__).resolve().parent.parent


def main(max_hold: int = 20) -> int:
    rows = []
    for plans_file in sorted((ROOT / "data").glob("*/plans.json")):
        day = plans_file.parent.name[:10]  # 장전 재계산 폴더(2026-10-12-0924)도 날짜만 씀
        for p in json.loads(plans_file.read_text(encoding="utf-8")):
            try:
                df = data.ohlcv(p["ticker"], (data.today_kst() - date.fromisoformat(day)).days + 10, min_rows=1)
            except data.DataUnavailable as e:
                print(f"건너뜀: {e}", file=sys.stderr)
                continue
            after = df[df.index > day]
            out = simulate_exit(after, p["entry"], p["stop"], p.get("targets") or [[p["target"], 1.0]], max_hold) if len(after) else {"result": "진행중"}
            rows.append({"date": plans_file.parent.name, "ticker": p["ticker"], "name": p["name"], "setup": p["setup"],
                         "verdict": p["verdict"], "result": out["result"]})
    if not rows:
        print("채점할 지난 계획이 없습니다.")
        return 0
    lines = ["# 자가학습 채점표", "", "| 날짜 | 종목 | 셋업 | 리스크 판정 | 결과 |", "|---|---|---|---|---|"]
    lines += [f"| {r['date']} | {r['name']} | {r['setup']} | {r['verdict']} | {r['result']} |" for r in rows]
    done = [r for r in rows if r["result"] != "진행중"]
    for setup in sorted({r["setup"] for r in done}):
        s = [r for r in done if r["setup"] == setup]
        hit = sum("익절" in r["result"] for r in s)
        lines.append(f"\n- {setup}: 끝난 계획 {len(s)}건 중 익절(1차 이상) {hit}건")
    out = ROOT / "reports" / "review.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"채점 완료 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
