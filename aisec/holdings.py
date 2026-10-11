"""보유 종목 점검: 이미 들고 있는 종목에 원칙(손절선·총노출 70%)을 대고 '제안'을 만듭니다.

판단은 대표님이 합니다. 이 코드는 규칙이 무엇을 말하는지만 숫자로 보여 줍니다.

규칙 (우선순위 순):
1. 종가가 손절선(평균단가 -stop_loss_pct%) 이하 → 전량 매도 제안
2. 주식 비중이 총노출 한도를 넘으면 → 초과분만큼 축소 제안
   축소 순서: 추세 이탈(20일선 < 60일선이고 종가 < 60일선) 종목 먼저, 그다음 손절선에 가까운 종목 먼저
3. 나머지 → 보유 유지, 손절선·목표가 안내

수동 실행 (데이터 연결 전, 앱 화면 가격으로):
    python -m aisec.holdings 052690=120600 066570=200500
"""
import math
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def review(portfolio: dict, prices: dict, risk: dict, trends: dict | None = None, target_rr: float = 2.0) -> dict:
    trends = trends or {}
    capital, stop_pct = risk["capital"], risk["stop_loss_pct"]
    rows = []
    for t, h in portfolio.items():
        px, avg = prices[t], h["avg_price"]
        stop = round(avg * (1 - stop_pct / 100))
        rows.append({
            "ticker": t, "name": h.get("name", t), "shares": h["shares"], "avg_price": avg, "price": px,
            "value": px * h["shares"], "pnl": (px - avg) * h["shares"], "pnl_pct": round((px / avg - 1) * 100, 2),
            "stop": stop, "to_stop_pct": round((stop / px - 1) * 100, 2),
            "target": round(avg + target_rr * (avg - stop)),
            "trend_broken": trends.get(t), "sell": 0, "action": "보유 유지", "reasons": [],
        })

    for r in rows:  # 1. 손절선
        if r["price"] <= r["stop"]:
            r.update(sell=r["shares"], action="전량 매도")
            r["reasons"].append(f"손절선 {r['stop']:,}원 이탈")

    total = sum(r["value"] for r in rows)
    limit = capital * risk["max_total_exposure_pct"] / 100
    excess = total - sum(r["sell"] * r["price"] for r in rows) - limit
    order = sorted([r for r in rows if r["sell"] < r["shares"]],
                   key=lambda r: (not r["trend_broken"], -r["to_stop_pct"]))  # 추세 이탈 먼저, 손절선 가까운 순
    for r in order:  # 2. 총노출
        if excess <= 0:
            break
        n = min(r["shares"] - r["sell"], math.ceil(excess / r["price"]))
        r["sell"] += n
        excess -= n * r["price"]
        r["action"] = "전량 매도" if r["sell"] == r["shares"] else f"{r['sell']}주 축소"
        why = "추세 이탈 종목 우선" if r["trend_broken"] else "손절선에 더 가까운 종목 우선"
        r["reasons"].append(f"주식 비중 {risk['max_total_exposure_pct']}% 초과분 정리 ({why})")

    for r in rows:
        r["after_shares"] = r["shares"] - r["sell"]
        if r["after_shares"]:
            r["reasons"].append(f"남은 {r['after_shares']}주: 손절선 {r['stop']:,}원(현재가 대비 {r['to_stop_pct']}%), "
                                f"목표가 {r['target']:,}원")
    after = sum(r["after_shares"] * r["price"] for r in rows)
    return {
        "before_pct": round(total / capital * 100, 1), "after_pct": round(after / capital * 100, 1),
        "limit_pct": risk["max_total_exposure_pct"], "cash_freed": sum(r["sell"] * r["price"] for r in rows),
        "rows": rows,
    }


def render(res: dict, as_of: str) -> str:
    lines = [f"## 보유 종목 점검 ({as_of})", "",
             f"주식 비중 {res['before_pct']}% → 제안대로 하면 {res['after_pct']}% (원칙 {res['limit_pct']}%), "
             f"확보 현금 {res['cash_freed']:,}원", "",
             "| 종목 | 보유 | 평균단가 | 현재가 | 손익 | 손절선 | 규칙 제안 |", "|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        lines.append(f"| {r['name']} | {r['shares']}주 | {r['avg_price']:,} | {r['price']:,} | "
                     f"{r['pnl']:+,}원 ({r['pnl_pct']:+}%) | {r['stop']:,} | **{r['action']}** |")
    lines.append("")
    for r in res["rows"]:
        lines.append(f"- {r['name']}: " + " / ".join(r["reasons"]))
    lines += ["", "- [ ] 제안대로   [ ] 일부만   [ ] 보류   ← 대표님 결재",
              "", "규칙이 낸 제안일 뿐 투자 권유가 아닙니다. 최종 판단은 결재자에게 있습니다."]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    prices = {k: int(v) for k, v in (a.split("=") for a in argv)}
    missing = [t for t in cfg["portfolio"] if t not in prices]
    if missing:
        print(f"가격이 필요합니다: {missing}  예) python -m aisec.holdings 052690=120600", file=sys.stderr)
        return 1
    print(render(review(cfg["portfolio"], prices, cfg["risk"], target_rr=cfg["plan"]["target_rr"]), "수동 입력 가격"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
