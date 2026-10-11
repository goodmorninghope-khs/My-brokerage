"""기술 지표 계산. 입력은 pykrx 형식의 일봉 DataFrame(시가·고가·저가·종가·거래량)."""
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(100)


def snapshot(ohlcv: pd.DataFrame) -> dict:
    """마지막 거래일 기준 지표 묶음."""
    close, volume = ohlcv["종가"], ohlcv["거래량"]
    ma20, ma60 = close.rolling(20).mean(), close.rolling(60).mean()
    high_52w = ohlcv["고가"].tail(250).max()
    last = float(close.iloc[-1])
    return {
        "date": ohlcv.index[-1].strftime("%Y-%m-%d"),
        "close": last,
        "change_1d_pct": round((last / close.iloc[-2] - 1) * 100, 2),
        "change_20d_pct": round((last / close.iloc[-21] - 1) * 100, 2),
        "ma20": round(float(ma20.iloc[-1]), 1),
        "ma60": round(float(ma60.iloc[-1]), 1),
        "rsi14": round(float(rsi(close).iloc[-1]), 1),
        "volume_ratio": round(float(volume.iloc[-1] / volume.tail(21).head(20).mean()), 2),
        "high_52w": float(high_52w),
        "from_52w_high_pct": round((last / high_52w - 1) * 100, 2),
    }
