"""아침 필터: 규칙마다 1점(가중치)씩 더해 상위 종목을 고릅니다."""

CHECKS = {
    "above_ma60": lambda s: s["close"] > s["ma60"],
    "ma20_over_ma60": lambda s: s["ma20"] > s["ma60"],
    "rsi_not_overheated": lambda s: s["rsi14"] < 70,
    "volume_surge": lambda s: s["volume_ratio"] >= 1.5,
    "near_52w_high": lambda s: s["from_52w_high_pct"] >= -10,
    "foreign_net_buy": lambda s: s.get("foreign_net_5d") is not None and s["foreign_net_5d"] > 0,
}


def score(snap: dict, rules: dict) -> tuple[float, list[str]]:
    passed = [name for name in rules if name in CHECKS and CHECKS[name](snap)]
    return sum(rules[n] for n in passed), passed


def rank(snaps: dict[str, dict], rules: dict, top_n: int) -> list[dict]:
    rows = []
    for ticker, snap in snaps.items():
        total, passed = score(snap, rules)
        rows.append({"ticker": ticker, "score": total, "passed": passed, **snap})
    rows.sort(key=lambda r: (r["score"], r["change_20d_pct"]), reverse=True)
    return rows[:top_n]
