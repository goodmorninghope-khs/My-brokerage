"""보유 종목 점검: 이미 들고 있는 종목에 원칙(손절선·총노출 70%)을 대고 '제안'을 만듭니다.

판단은 대표님이 합니다. 이 코드는 규칙이 무엇을 말하는지만 숫자로 보여 줍니다.

규칙 (우선순위 순):
1. 종가가 손절선(평균단가 -stop_loss_pct%) 이하 → 전량 매도 제안
   종가가 익절가(평균단가 +10%, +15%) 이상 → 단계별 익절 제안 (1차: 절반, 2차: 나머지)
2. 주식 비중이 총노출 한도를 넘으면 → 초과분만큼 축소 제안
   축소 순서: 추세 이탈(20일선 < 60일선이고 종가 < 60일선) 종목 먼저, 그다음 손절선에 가까운 종목 먼저
3. 나머지 → 보유 유지, 손절선·목표가 안내
* 경고(제안은 바꾸지 않고 눈에 띄게만): 장중 저가가 손절선을 건드린 날, 손절선 아래 마감이 이어진 날 수,
  손실이 한 번 손절 한도(자산의 risk_per_trade_pct%)를 넘었는지. 백테스트는 '저가가 손절선 이하'면 손절로 치므로
  종가 기준 제안과 어긋나는 날을 숨기지 않습니다.
* 한 종목 비중 한도(max_position_pct)는 보유 종목에 '유연'하게: 넘으면 경고와 참고 수량만 보여 주고 강제 축소하지 않습니다.

수동 실행 (데이터 연결 전, 앱 화면 가격으로):
    python -m aisec.holdings 052690=120600 066570=200500
"""
import math
import sys
from pathlib import Path

import yaml

from .backtest import targets_for

ROOT = Path(__file__).resolve().parent.parent


def bar_detail(df, stop: float) -> dict:
    """보유 점검에 붙일 마지막 일봉·이평선·손절선 이탈 일수. df 는 그날까지의 일봉."""
    c = df["종가"]
    last = df.iloc[-1]
    below = 0
    for v in reversed(c.tolist()):  # 손절선 이하 마감이 며칠째 이어졌나
        if v > stop:
            break
        below += 1
    vol20 = df["거래량"].tail(21).iloc[:-1].mean()
    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "open": round(float(last["시가"])), "high": round(float(last["고가"])), "low": round(float(last["저가"])),
        "close": round(float(last["종가"])), "change_1d_pct": round((float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100, 2),
        "volume": int(last["거래량"]), "volume_ratio": round(float(last["거래량"]) / vol20, 2) if vol20 else None,
        "ma20": round(float(c.rolling(20).mean().iloc[-1]), 1), "ma60": round(float(c.rolling(60).mean().iloc[-1]), 1),
        "days_below_stop": below,
    }


def review(portfolio: dict, prices: dict, risk: dict, trends: dict | None = None,
           take_profit: list | None = None, details: dict | None = None) -> dict:
    take_profit = take_profit or [[10, 0.5], [15, 0.5]]
    trends = trends or {}
    details = details or {}
    capital, stop_pct = risk["capital"], risk["stop_loss_pct"]
    rows = []
    for t, h in portfolio.items():
        px, avg = prices[t], h["avg_price"]
        stop = round(avg * (1 - stop_pct / 100))
        rows.append({
            "ticker": t, "name": h.get("name", t), "shares": h["shares"], "avg_price": avg, "price": px,
            "value": px * h["shares"], "pnl": (px - avg) * h["shares"], "pnl_pct": round((px / avg - 1) * 100, 2),
            "stop": stop, "to_stop_pct": round((stop / px - 1) * 100, 2),
            "targets": targets_for(avg, take_profit),
            "trend_broken": trends.get(t), "sell": 0, "action": "보유 유지", "reasons": [], "warnings": [],
            "detail": details.get(t), "loss_pct_capital": round((px - avg) * h["shares"] / capital * 100, 2),
        })

    for r in rows:  # 1. 손절선
        if r["price"] <= r["stop"]:
            r.update(sell=r["shares"], action="전량 매도")
            r["reasons"].append(f"손절선 {r['stop']:,}원 이탈")

        elif r["price"] >= r["targets"][0][0]:  # 익절 단계
            reached = [t for t in r["targets"] if r["price"] >= t[0]]
            portion = sum(t[1] for t in reached)
            # 나눠 팔 수 없을 만큼 적으면(예: 1주) 1차 익절가에서 최소 1주를 팝니다.
            n = r["shares"] if portion >= 1 else max(1, math.floor(r["shares"] * portion))
            r.update(sell=n, action="전량 익절" if n == r["shares"] else f"{n}주 익절")
            r["reasons"].append(f"{len(reached)}차 익절가 {reached[-1][0]:,}원 도달")

    for r in rows:  # 경고: 제안(action)은 바꾸지 않습니다
        d, limit_pct = r["detail"], risk.get("risk_per_trade_pct", 1)
        if d and d["low"] <= r["stop"] < r["price"]:
            r["warnings"].append(f"장중 저가 {d['low']:,}원이 손절선 {r['stop']:,}원 아래였습니다(종가는 위). "
                                 "감시 손절이 걸려 있었다면 체결됐을 자리이고, 백테스트는 이런 날을 손절로 칩니다")
        if d and d["days_below_stop"] >= 2:
            r["warnings"].append(f"손절선 아래 마감 {d['days_below_stop']}거래일째")
        if r["loss_pct_capital"] <= -limit_pct:
            r["warnings"].append(f"손실이 자산의 {abs(r['loss_pct_capital'])}% — 한 번 손절 한도 {limit_pct}% 초과")

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
        weight = r["after_shares"] * r["price"] / capital * 100
        r["weight_pct"] = round(weight, 1)
        if weight > risk["max_position_pct"] + risk.get("position_flex_pct", 0):
            keep = math.floor(capital * risk["max_position_pct"] / 100 / r["price"])
            r["reasons"].append(f"⚠ 한 종목 비중 {weight:.1f}% (한도 {risk['max_position_pct']}%, 유연 적용) — "
                                f"한도에 맞추려면 {keep}주까지 줄여야 함. 참고만, 강제 아님")
        if r["after_shares"]:
            tp_text = (" → ".join(f"{t[0]:,}원({int(t[1] * 100)}%)" for t in r["targets"])
                       if r["after_shares"] > 1 else f"{r['targets'][0][0]:,}원(1주 전량)")
            r["reasons"].append(f"남은 {r['after_shares']}주: 손절선 {r['stop']:,}원(현재가 대비 {r['to_stop_pct']}%), "
                                f"익절가 {tp_text}")
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
             "| 종목 | 보유 | 평균단가 | 현재가 | 손익 | 손절선 | 규칙 제안 | 제안 후 비중 |", "|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        lines.append(f"| {r['name']} | {r['shares']}주 | {r['avg_price']:,} | {r['price']:,} | "
                     f"{r['pnl']:+,}원 ({r['pnl_pct']:+}%) | {r['stop']:,} | **{r['action']}** | {r['weight_pct']}% |")
    lines.append("")
    for r in res["rows"]:
        lines.append(f"- {r['name']}: " + " / ".join(r["reasons"]))
        for w in r.get("warnings", []):
            lines.append(f"  - ⚠ {w}")
        d = r.get("detail")
        if d:
            vol = f" · 거래량 평소의 {d['volume_ratio']}배" if d.get("volume_ratio") else ""
            lines.append(f"  - {d['date']} 일봉: 시가 {d['open']:,} · 고가 {d['high']:,} · 저가 {d['low']:,} · 종가 {d['close']:,}"
                         f"({d['change_1d_pct']:+}%){vol}")
            lines.append(f"  - 이평선: 20일선 {d['ma20']:,}(종가가 {(d['close'] / d['ma20'] - 1) * 100:+.2f}%) · "
                         f"60일선 {d['ma60']:,}(종가가 {(d['close'] / d['ma60'] - 1) * 100:+.2f}%) · "
                         f"손익은 자산의 {r['loss_pct_capital']:+}%")
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
    print(render(review(cfg["portfolio"], prices, cfg["risk"], take_profit=cfg["plan"]["take_profit"]), "수동 입력 가격"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
