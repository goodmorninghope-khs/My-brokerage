"""마켓부(숫자 부분): 코스피 지수로 시장 국면을 판정합니다. 해석은 market-desk 에이전트가."""
import pandas as pd

# 태세는 방향만 말합니다. '어떤 셋업이 맞는지'는 문구로 못 박지 않고,
# 그날의 국면별 백테스트 숫자(fit_setups)로 정합니다. 문구와 숫자가 어긋나는 일을 막기 위해서입니다.
STANCE = {
    "상승장": "공격: 국면 성적이 플러스인 셋업 우선",
    "하락장": "방어: 신규 매수 최소화, 국면 성적이 플러스인 셋업만 소액",
    "횡보장": "중립: 비중 축소, 국면 성적이 플러스인 셋업만",
}
SHOCK_1D_PCT, SHOCK_5D_PCT = 3.0, -7.0  # 하루 ±3% 또는 5일 -7%면 급변 → 신규 매수 차단
MIN_EDGE_PCT = 0.3  # 국면 평균 수익이 이보다 작으면 '우위 미미'(수수료·미끄러짐에 묻히는 크기)


def regime_series(index_df: pd.DataFrame) -> pd.Series:
    """날짜마다 그날까지의 지수로 국면을 판정합니다(미래 데이터 금지). 백테스트와 실전이 같이 씁니다."""
    c = index_df["종가"]
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    up = (c > ma60) & (ma20 > ma60)
    down = (c < ma60) & (ma20 < ma60)
    out = pd.Series("횡보장", index=c.index)
    out[up], out[down] = "상승장", "하락장"
    out[ma60.isna()] = None
    return out


def regime(index_df: pd.DataFrame) -> dict:
    c = index_df["종가"]
    last = c.iloc[-1]
    chg1, chg5 = (last / c.iloc[-2] - 1) * 100, (last / c.iloc[-6] - 1) * 100
    name = regime_series(index_df).iloc[-1]
    return {
        "date": index_df.index[-1].strftime("%Y-%m-%d"),
        "kospi": round(float(last), 2),
        "change_1d_pct": round(chg1, 2),
        "change_5d_pct": round(chg5, 2),
        "regime": name,
        "stance": STANCE[name],
        "shock": bool(abs(chg1) >= SHOCK_1D_PCT or chg5 <= SHOCK_5D_PCT),
    }


def shock_outlook(index_df: pd.DataFrame, days: int = 3) -> list[dict]:
    """다음 거래일들의 종가가 어느 구간에 들어와야 급변 규칙이 꺼지는지(또는 꺼진 채 유지되는지).

    k번째 거래일의 5일 기준 종가는 이미 알려진 값입니다(k ≤ 5). 하루 ±3% 구간은 바로 다음 거래일(k=1)만
    미리 알 수 있으므로, k ≥ 2 는 5일 기준 하한만 줍니다. regime() 과 같은 상수를 씁니다.
    """
    c = index_df["종가"]
    out = []
    for k in range(1, days + 1):
        base = float(c.iloc[-6 + k])  # 그날 종가 기준 5거래일 전 종가
        floor5 = base * (1 + SHOCK_5D_PCT / 100)
        row = {"step": k, "base_date": c.index[-6 + k].strftime("%Y-%m-%d"), "floor_5d": round(floor5, 1)}
        if k == 1:
            last = float(c.iloc[-1])
            lo1, hi1 = last * (1 - SHOCK_1D_PCT / 100), last * (1 + SHOCK_1D_PCT / 100)
            lower = max(lo1, floor5)
            row.update(low_1d=round(lo1, 1), high_1d=round(hi1, 1), lower=round(lower, 1), upper=round(hi1, 1),
                       possible=bool(lower < hi1),
                       lower_pct=round((lower / last - 1) * 100, 2), upper_pct=SHOCK_1D_PCT)
        out.append(row)
    return out


def fit_setups(regime_stats: dict, regime_name: str) -> dict:
    """오늘 국면에서 셋업별 과거 성적을 '맞는 / 우위 미미 / 피할 / 표본 부족'으로 나눕니다(숫자 근거)."""
    out = {"fit": [], "thin": [], "avoid": [], "unknown": []}
    for setup, by in sorted(regime_stats.items()):
        st = by.get(regime_name)
        if not st or not st.get("reliable"):
            out["unknown"].append({"setup": setup, "trades": (st or {}).get("trades", 0)})
            continue
        row = {"setup": setup, "trades": st["trades"], "win_rate_pct": st["win_rate_pct"], "avg_ret_pct": st["avg_ret_pct"]}
        key = "fit" if st["avg_ret_pct"] >= MIN_EDGE_PCT else "thin" if st["avg_ret_pct"] > 0 else "avoid"
        out[key].append(row)
    for k in ("fit", "thin", "avoid"):
        out[k].sort(key=lambda r: -r["avg_ret_pct"])
    return out
