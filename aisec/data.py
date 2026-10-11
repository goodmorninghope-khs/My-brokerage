"""KRX 데이터 수집 (pykrx). 주가는 로그인 없이, 수급·PER 등은 KRX 무료 회원 로그인(KRX_ID/KRX_PW) 필요."""
import os
from datetime import date, timedelta

import pandas as pd


class DataUnavailable(RuntimeError):
    pass


def _stock():
    from pykrx import stock  # import 시 로그인 메시지가 출력되므로 지연 로딩
    return stock


def ohlcv(ticker: str, lookback_days: int) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=lookback_days)
    try:
        df = _stock().get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
    except Exception as e:  # 네트워크 차단·사이트 변경 등
        raise DataUnavailable(f"{ticker} 주가를 받지 못했습니다: {e}") from e
    if df is None or len(df) < 61:
        raise DataUnavailable(f"{ticker} 주가 데이터가 비었거나 너무 짧습니다 ({0 if df is None else len(df)}일).")
    return df


def foreign_net_5d(ticker: str) -> float | None:
    """최근 5영업일 외국인 순매수 금액(원). 로그인 정보가 없으면 None."""
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        return None
    end = date.today()
    start = end - timedelta(days=12)
    try:
        df = _stock().get_market_trading_value_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
        return float(df["외국인합계"].tail(5).sum())
    except Exception:
        return None


def fundamental(ticker: str) -> dict | None:
    """PER·PBR·배당수익률 (KRX 로그인 필요). 실패하면 None."""
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        return None
    end = date.today()
    start = end - timedelta(days=10)
    try:
        df = _stock().get_market_fundamental(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
        row = df.iloc[-1]
        return {k: float(row[k]) for k in ("PER", "PBR", "DIV", "EPS", "BPS") if k in row}
    except Exception:
        return None
