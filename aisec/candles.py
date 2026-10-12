"""캔들차트 신호: 일봉 모양을 규칙으로 읽습니다. 그날까지의 데이터만 씁니다.

반전 신호는 '앞선 흐름'이 있어야 의미가 있습니다:
  상승 반전형(망치형·상승장악형·샛별형)은 직전 5일 하락 뒤에만,
  하락 반전형(유성형·하락장악형)은 직전 5일 상승 뒤에만 인정합니다.
교과서 설명을 믿지 않고, backtest.run_candles 로 우리 종목에서의 실제 성적을 먼저 봅니다.
"""
import pandas as pd

BULLISH = ["망치형", "상승장악형", "샛별형", "적삼병"]
BEARISH = ["유성형", "하락장악형"]
MEANING = {
    "망치형": "하락 끝에 긴 아래꼬리 — 장중 매도를 사자 세력이 받아냄",
    "상승장악형": "하락 끝에 양봉이 전날 음봉 몸통을 통째로 감쌈 — 주도권 교체",
    "샛별형": "큰 음봉 → 작은 몸통(망설임) → 큰 양봉 — 바닥 다지기",
    "적삼병": "윗꼬리 짧은 양봉 3개가 계단식 상승 — 매수세 지속",
    "유성형": "상승 끝에 긴 윗꼬리 — 고점에서 매도에 밀림",
    "하락장악형": "상승 끝에 음봉이 전날 양봉을 통째로 감쌈 — 매도 우위 전환",
    "도지": "시가≈종가 — 매수·매도 힘겨루기, 방향 전환 전조일 수 있음",
}


def patterns(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["시가"], df["고가"], df["저가"], df["종가"]
    rng = (h - l).where(h > l)
    body = (c - o).abs()
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - l
    green, red = c > o, c < o
    down = c.shift(1) < c.shift(6)   # 직전 5일 하락
    up = c.shift(1) > c.shift(6)     # 직전 5일 상승
    o1, c1, b1 = o.shift(1), c.shift(1), body.shift(1)
    p = pd.DataFrame(index=df.index)
    p["망치형"] = down & (lower >= 0.6 * rng) & (upper <= 0.15 * rng)
    p["상승장악형"] = down & red.shift(1, fill_value=False) & green & (o <= c1) & (c >= o1) & (body > b1)
    p["샛별형"] = (down.shift(1, fill_value=False) & red.shift(2, fill_value=False)
                  & (body.shift(2) >= 0.6 * rng.shift(2)) & (b1 <= 0.3 * rng.shift(1))
                  & green & (c >= (o.shift(2) + c.shift(2)) / 2))
    p["적삼병"] = (green & green.shift(1, fill_value=False) & green.shift(2, fill_value=False)
                  & (c > c1) & (c1 > c.shift(2)) & (o > o1) & (o <= c1) & (o1 <= c.shift(2))
                  & (upper <= 0.3 * body) & (upper.shift(1) <= 0.3 * b1))
    p["유성형"] = up & (upper >= 0.6 * rng) & (lower <= 0.15 * rng)
    p["하락장악형"] = up & green.shift(1, fill_value=False) & red & (o >= c1) & (c <= o1) & (body > b1)
    p["도지"] = body <= 0.1 * rng
    return p.fillna(False).astype(bool)


def recent(df: pd.DataFrame, days: int = 3) -> list[dict]:
    """최근 N거래일에 나온 캔들 신호 (최신이 먼저)."""
    p = patterns(df).tail(days)
    out = []
    for day, row in p.iloc[::-1].iterrows():
        for name in row.index[row.values]:
            kind = "상승" if name in BULLISH else "하락" if name in BEARISH else "중립"
            out.append({"date": day.strftime("%Y-%m-%d"), "pattern": name, "kind": kind, "meaning": MEANING[name]})
    return out
