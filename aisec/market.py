"""마켓부(숫자 부분): 코스피 지수로 시장 국면을 판정합니다. 해석은 market-desk 에이전트가."""
import pandas as pd

STANCE = {
    "상승장": "공격: 돌파·모멘텀·추세지속 우선",
    "하락장": "방어: 신규 매수 최소화, 반전 셋업만 소액",
    "횡보장": "중립: 눌림목·반전 위주, 비중 축소",
}


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
        "shock": bool(abs(chg1) >= 3 or chg5 <= -7),  # 하루 ±3% 또는 5일 -7%면 급변 → 신규 매수 차단
    }
