"""스크리닝부: 광고의 5가지 셋업을 '규칙'으로 판별합니다. AI 해석 없이 숫자만으로 결정.

모든 함수는 그날까지의 데이터만 씁니다(미래 데이터 금지) — 백테스트와 실전이 같은 코드를 씁니다.
"""
import pandas as pd

from .indicators import rsi

SETUP_NAMES = ["돌파", "눌림목", "모멘텀", "추세지속", "반전"]

INVALIDATION = {
    "돌파": "종가가 돌파한 저항선 아래로 다시 내려오면 무효",
    "눌림목": "종가가 60일선 아래로 내려가면 무효",
    "모멘텀": "종가가 20일선 아래로 내려가면 무효",
    "추세지속": "20일선이 60일선 아래로 내려가면(역배열) 무효",
    "반전": "최근 10일 최저가를 깨면 무효",
}


def features(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = df["종가"], df["고가"], df["저가"], df["거래량"]
    f = pd.DataFrame(index=df.index)
    f["close"], f["high"], f["low"], f["open"] = c, h, l, df["시가"]
    f["ma20"], f["ma60"], f["ma120"] = c.rolling(20).mean(), c.rolling(60).mean(), c.rolling(120).mean()
    f["rsi"] = rsi(c)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f["atr"] = tr.rolling(14).mean()
    f["vol_ratio"] = v / v.shift(1).rolling(20).mean()
    f["high20_prev"] = h.shift(1).rolling(20).max()
    f["low10"] = l.rolling(10).min()
    f["rsi_min10"] = f["rsi"].rolling(10).min()
    f["ret20"] = c / c.shift(20) - 1
    f["ma20_5ago"] = f["ma20"].shift(5)
    f["prev_close"], f["prev_ma20"] = c.shift(), f["ma20"].shift()
    return f


def detect(r: pd.Series) -> list[dict]:
    """하루치 특징값(row)에서 성립한 셋업 목록. 각 셋업은 손절 기준가를 함께 냅니다."""
    if r[["ma120", "atr", "high20_prev", "vol_ratio"]].isna().any():
        return []
    c, atr = r["close"], r["atr"]
    found = []
    if c > r["high20_prev"] and r["vol_ratio"] >= 1.5:
        found.append(("돌파", r["high20_prev"] - 0.5 * atr, "거래량을 동반해 20일 고점을 넘었습니다"))
    if r["ma20"] > r["ma60"] and r["low"] <= r["ma20"] * 1.01 and c >= r["ma20"] and 40 <= r["rsi"] <= 60:
        found.append(("눌림목", r["low10"] - 0.5 * atr, "상승 추세 중 20일선까지 밀렸다가 지지받았습니다"))
    if r["ret20"] >= 0.10 and 55 <= r["rsi"] <= 70 and c > r["ma20"]:
        found.append(("모멘텀", c - 2 * atr, "20일간 10% 이상 오르며 과열 전 구간입니다"))
    if c > r["ma20"] > r["ma60"] > r["ma120"] and r["ma20"] > r["ma20_5ago"]:
        found.append(("추세지속", r["ma20"] - atr, "20·60·120일선 정배열, 20일선이 오르는 중입니다"))
    if r["rsi_min10"] < 30 and r["prev_close"] < r["prev_ma20"] and c > r["ma20"]:
        found.append(("반전", r["low10"] - 0.5 * atr, "과매도(RSI<30) 뒤 20일선을 위로 뚫었습니다"))
    return [{"setup": n, "stop": round(float(s)), "why": w} for n, s, w in found if s < c]


def near(r: pd.Series) -> list[dict]:
    """셋업 '임박' 목록: 아직 성립하지 않았지만 조건 하나만 남은 종목과 그 신호 가격."""
    if r[["ma120", "atr", "high20_prev"]].isna().any():
        return []
    c, out = r["close"], []
    uptrend = r["ma20"] > r["ma60"] and c > r["ma60"]
    if uptrend and r["high20_prev"] * 0.97 <= c <= r["high20_prev"]:
        out.append({"setup": "돌파", "trigger": round(float(r["high20_prev"])),
                    "gap_pct": round((r["high20_prev"] / c - 1) * 100, 2),
                    "condition": "이 가격을 거래량 1.5배 이상으로 넘으면 돌파 신호"})
    if uptrend and r["ma20"] < c <= r["ma20"] * 1.03:
        out.append({"setup": "눌림목", "trigger": round(float(r["ma20"])),
                    "gap_pct": round((r["ma20"] / c - 1) * 100, 2),
                    "condition": "20일선까지 내려와 지지받고(종가가 20일선 위) RSI 40~60이면 눌림목 신호"})
    return out
