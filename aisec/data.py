"""KRX 데이터 수집 (pykrx). 주가는 로그인 없이, 수급·PER 등은 KRX 무료 회원 로그인(KRX_ID/KRX_PW) 필요."""
import os
from datetime import date, timedelta

import pandas as pd


def today_kst() -> date:
    """서버 시계(UTC)가 아니라 한국 날짜."""
    from datetime import datetime, timezone
    return datetime.now(timezone(timedelta(hours=9))).date()


class DataUnavailable(RuntimeError):
    pass


def _stock():
    from pykrx import stock  # import 시 로그인 메시지가 출력되므로 지연 로딩
    from pykrx.website.naver.core import NaverWebIo

    # pykrx 는 네이버 차트에 http:// 로 접속합니다. 클라우드 환경은 https 만 통과시키므로 바꿔 줍니다.
    NaverWebIo.url = property(lambda self: "https://fchart.stock.naver.com/sise.nhn")
    return stock


def ohlcv(ticker: str, lookback_days: int, min_rows: int = 130) -> pd.DataFrame:
    end = today_kst()
    start = end - timedelta(days=lookback_days)
    try:
        df = _stock().get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
    except Exception as e:  # 네트워크 차단·사이트 변경 등
        raise DataUnavailable(f"{ticker} 주가를 받지 못했습니다: {e}") from e
    if df is None or len(df) < min_rows:
        raise DataUnavailable(f"{ticker} 주가 데이터가 비었거나 너무 짧습니다 ({0 if df is None else len(df)}일).")
    return df


def kospi(lookback_days: int = 200) -> pd.DataFrame:
    """코스피 지수 일봉 (KRX)."""
    end = today_kst()
    start = end - timedelta(days=lookback_days)
    try:
        df = _stock().get_index_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001")
    except Exception as e:
        raise DataUnavailable(f"코스피 지수를 받지 못했습니다: {e}") from e
    if df is None or len(df) < 61:
        raise DataUnavailable("코스피 지수 데이터가 비었거나 너무 짧습니다.")
    return df


def foreign_net_5d(ticker: str) -> float | None:
    """최근 5영업일 외국인 순매수 금액(원). 로그인 정보가 없으면 None."""
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        return None
    end = today_kst()
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
    end = today_kst()
    start = end - timedelta(days=10)
    try:
        df = _stock().get_market_fundamental(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
        row = df.iloc[-1]
        return {k: float(row[k]) for k in ("PER", "PBR", "DIV", "EPS", "BPS") if k in row}
    except Exception:
        return None


def is_preferred(ticker: str) -> bool:
    """우선주 판별: 보통주 코드는 0으로 끝나고, 우선주는 5·7·9 등으로 끝납니다 (예: 005935)."""
    return not ticker.endswith("0")


def market_cap_top(count: int, market: str = "ALL", exclude_preferred: bool = True) -> dict[str, str]:
    """가장 최근 영업일 기준 시가총액 상위 종목 {코드: 종목명}."""
    try:
        stock = _stock()
        day = stock.get_nearest_business_day_in_a_week()
        df = stock.get_market_cap(day, market=market).sort_values("시가총액", ascending=False)
    except Exception as e:
        raise DataUnavailable(f"시가총액 순위를 받지 못했습니다: {e}") from e
    if df is None or df.empty:
        raise DataUnavailable("시가총액 순위가 비어 있습니다.")
    tickers = [t for t in df.index if not (exclude_preferred and is_preferred(t))][:count]
    return {t: stock.get_market_ticker_name(t) for t in tickers}
