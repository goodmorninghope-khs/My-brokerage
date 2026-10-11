"""마켓부(숫자 부분): 코스피 지수로 시장 국면을 판정합니다. 해석은 market-desk 에이전트가."""
import pandas as pd


def regime(index_df: pd.DataFrame) -> dict:
    c = index_df["종가"]
    ma20, ma60 = c.rolling(20).mean().iloc[-1], c.rolling(60).mean().iloc[-1]
    last = c.iloc[-1]
    chg1, chg5 = (last / c.iloc[-2] - 1) * 100, (last / c.iloc[-6] - 1) * 100
    if last > ma60 and ma20 > ma60:
        name, stance = "상승장", "공격: 돌파·모멘텀·추세지속 우선"
    elif last < ma60 and ma20 < ma60:
        name, stance = "하락장", "방어: 신규 매수 최소화, 반전 셋업만 소액"
    else:
        name, stance = "횡보장", "중립: 눌림목·반전 위주, 비중 축소"
    return {
        "date": index_df.index[-1].strftime("%Y-%m-%d"),
        "kospi": round(float(last), 2),
        "change_1d_pct": round(chg1, 2),
        "change_5d_pct": round(chg5, 2),
        "regime": name,
        "stance": stance,
        "shock": bool(abs(chg1) >= 3 or chg5 <= -7),  # 하루 ±3% 또는 5일 -7%면 급변 → 신규 매수 차단
    }
