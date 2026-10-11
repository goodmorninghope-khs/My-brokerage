"""계산 로직 단위 테스트. 아래 숫자는 테스트용 합성 데이터이며 실제 시세가 아닙니다."""
import pandas as pd

from aisec import indicators, screen


def _frame(closes, volumes=None):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    volumes = volumes or [1000] * len(closes)
    return pd.DataFrame({"시가": closes, "고가": closes, "저가": closes, "종가": closes, "거래량": volumes}, index=idx)


def test_uptrend_snapshot():
    snap = indicators.snapshot(_frame([100 + i for i in range(100)]))
    assert snap["close"] == 199
    assert snap["ma20"] > snap["ma60"]
    assert snap["from_52w_high_pct"] == 0
    assert snap["rsi14"] > 90  # 하락일이 없으면 RSI는 100에 가깝다


def test_volume_surge_ratio():
    vols = [1000] * 99 + [3000]
    assert indicators.snapshot(_frame([100] * 100, vols))["volume_ratio"] == 3.0


def test_rank_orders_by_score():
    up = indicators.snapshot(_frame([100 + i for i in range(100)]))
    down = indicators.snapshot(_frame([200 - i for i in range(100)]))
    rules = {"above_ma60": 1, "ma20_over_ma60": 1, "foreign_net_buy": 1}
    picks = screen.rank({"UP": up, "DOWN": down}, rules, top_n=1)
    assert picks[0]["ticker"] == "UP"
    assert "foreign_net_buy" not in picks[0]["passed"]  # 수급 데이터 없으면 통과로 치지 않음


def test_build_universe_merges_watchlist_and_top():
    from aisec.cli import build_universe
    from aisec.data import is_preferred

    u = build_universe({"005930": "삼성전자", "000990": "DB하이텍"}, {"005930": "삼성전자", "373220": "LG에너지솔루션"})
    assert list(u) == ["005930", "000990", "373220"]
    assert u["005930"] == {"name": "삼성전자", "source": "관심종목", "mcap_rank": 1}
    assert u["373220"]["source"] == "시총상위"
    assert is_preferred("005935") and not is_preferred("005930")
