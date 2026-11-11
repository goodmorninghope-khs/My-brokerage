"""스크리닝부(강도 점수): 다른 분석 도구에서 빌려 온 세 가지 잣대. 모두 그날까지의 데이터만 씁니다.

- 상대강도(RS): IBD 의 RS 등급처럼 3·6·9·12개월 수익률을 최근에 더 무게를 두어 섞고,
  코스피보다 얼마나 강했는지를 우리 종목들 안에서 1~99 등수로 매깁니다.
- 추세 템플릿: 미너비니의 체크리스트를 우리 이평선(20·60·120일)에 맞춘 7개 항목.
- 종합 점수: 위 둘과 국면별 셋업 성적·캔들을 합친 0~100점. 점수표를 그대로 보여 주어
  왜 이 순위인지 결재자가 확인할 수 있게 합니다.
"""
import pandas as pd

FULL_EDGE_PCT = 1.0  # 국면 평균 수익이 이 값 이상이면 '국면별 셋업 성적' 만점
RS_WEIGHTS = [(63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)]  # (거래일, 가중치): 최근 3개월에 두 배 무게


def weighted_return(close: pd.Series) -> float | None:
    """가중 수익률(%). 1년치가 안 되면 있는 구간만 가중치를 다시 나눠 씁니다."""
    parts = [(w, close.iloc[-1] / close.iloc[-1 - n] - 1) for n, w in RS_WEIGHTS if len(close) > n]
    if not parts:
        return None
    total = sum(w for w, _ in parts)
    return sum(w * r for w, r in parts) / total * 100


def rs_ratings(closes: dict[str, pd.Series], index_close: pd.Series | None = None) -> dict[str, dict]:
    """종목별 {rs_raw: 코스피 대비 초과 가중수익률(%p), rs_rating: 1~99, rs_20d: 20일 초과수익률(%p)}."""
    bench = weighted_return(index_close) if index_close is not None else 0.0
    bench20 = (index_close.iloc[-1] / index_close.iloc[-21] - 1) * 100 if index_close is not None and len(index_close) > 20 else 0.0
    raw = {}
    for t, c in closes.items():
        w = weighted_return(c)
        if w is not None:
            raw[t] = round(w - (bench or 0.0), 2)
    ranked = sorted(raw, key=raw.get)
    n = len(ranked)
    out = {}
    for i, t in enumerate(ranked):
        c = closes[t]
        r20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100 - bench20 if len(c) > 20 else None
        out[t] = {"rs_raw": raw[t], "rs_rating": 1 if n == 1 else round(1 + 98 * i / (n - 1)),
                  "rs_20d": None if r20 is None else round(r20, 2)}
    return out


def excess_20d_history(close: pd.Series, index_close: pd.Series | None, n: int = 5) -> list[dict]:
    """최근 n거래일의 '20일 코스피 대비 초과수익률(%p)' 흐름. 보유 종목이 시장보다 약해지는지 미리 봅니다."""
    if index_close is None:
        return []
    idx = index_close.reindex(close.index).ffill()
    ex = ((close / close.shift(20) - 1) - (idx / idx.shift(20) - 1)) * 100
    return [{"date": d.strftime("%Y-%m-%d"), "rs_20d": round(float(v), 2)} for d, v in ex.dropna().tail(n).items()]


def trend_template(df: pd.DataFrame, rs_rating: int | None = None) -> dict:
    """미너비니 추세 템플릿(우리 이평선 버전). 통과 개수와 항목별 결과."""
    c = df["종가"]
    ma60, ma120 = c.rolling(60).mean(), c.rolling(120).mean()
    last = c.iloc[-1]
    hi, lo = df["고가"].tail(250).max(), df["저가"].tail(250).min()
    checks = {
        "종가 > 60일선": last > ma60.iloc[-1],
        "종가 > 120일선": last > ma120.iloc[-1],
        "60일선 > 120일선": ma60.iloc[-1] > ma120.iloc[-1],
        "120일선 상승(20일 전보다 위)": len(ma120.dropna()) > 20 and ma120.iloc[-1] > ma120.iloc[-21],
        "52주 저점보다 25% 이상 위": last >= lo * 1.25,
        "52주 고점에서 25% 이내": last >= hi * 0.75,
        "상대강도 70 이상": rs_rating is not None and rs_rating >= 70,
    }
    checks = {k: bool(v) for k, v in checks.items()}
    return {"passed": sum(checks.values()), "total": len(checks), "checks": checks}


def score(regime_stats: dict | None, pooled_stats: dict | None, rs: dict | None, template: dict | None,
          ticker_stats: dict | None, candle_bonus: bool, candle_warning: bool) -> dict:
    """종합 점수(0~100)와 점수표. 리스크 판정과는 별개이며, 같은 리스크 판정 안에서 순서를 정할 때만 씁니다."""
    parts = {}
    # 1) 지금 국면에서 이 셋업이 통했나 (30점): 평균 수익에 비례(+1.0% 이상이면 만점, 0% 이하면 0점).
    #    예전에는 평균이 +0.02%여도 30점 만점이었습니다. 우위가 얇으면 점수도 얇게 줍니다.
    #    국면별 표본이 20번 미만이면 전체 성적을 절반(15점 만점)만 반영합니다.
    def edge(st: dict, full: int) -> int:
        return round(full * min(1.0, max(0.0, st.get("avg_ret_pct", 0)) / FULL_EDGE_PCT))
    if regime_stats and regime_stats.get("reliable"):
        parts["국면별 셋업 성적"] = edge(regime_stats, 30)
    elif pooled_stats and pooled_stats.get("reliable"):
        parts["국면별 셋업 성적"] = edge(pooled_stats, 15)
    else:
        parts["국면별 셋업 성적"] = 0
    # 2) 상대강도 (25점)
    parts["상대강도"] = round(25 * rs["rs_rating"] / 99) if rs else 0
    # 3) 추세 템플릿 (20점)
    parts["추세 템플릿"] = round(20 * template["passed"] / template["total"]) if template else 0
    # 4) 이 종목·이 셋업 백테스트 (10점): 5번 이상이고 평균 수익이 양수
    parts["종목 백테스트"] = 10 if ticker_stats and ticker_stats.get("trades", 0) >= 5 and ticker_stats.get("avg_ret_pct", -1) > 0 else 0
    # 5) 캔들 (15점 / 경고 -20점)
    parts["검증된 상승 캔들"] = 15 if candle_bonus else 0
    parts["하락 캔들 경고"] = -20 if candle_warning else 0
    return {"total": max(0, sum(parts.values())), "parts": parts}
