"""백테스트: 셋업 규칙을 과거 데이터에 그대로 돌려 승률·손익비·최대낙폭을 잽니다.

가정 (보수적으로):
- 신호가 뜬 다음 날 시가에 진입. 다음 날 시가가 진입 구간을 벗어나도 그냥 진입(단순화).
- 손절: 셋업 손절가와 -stop_loss_pct% 중 진입가에 가까운 쪽. 같은 날 손절·익절이 겹치면 손절로 칩니다.
- 익절: take_profit 단계대로 나눠 팝니다 (예: +10%에 절반, +15%에 나머지).
- max_hold 거래일이 지나면 종가에 정리.
- 거래마다 자산의 risk_pct% 만 걸었다고 보고 자산 곡선을 계산(리스크관리부 규칙과 동일).
- 수수료·세금 0.25%를 거래마다 뺍니다.
"""
import pandas as pd

from .setups import detect, features


def targets_for(price: float, take_profit: list) -> list:
    """[[수익률%, 비율], ...] → [[목표가, 비율], ...]"""
    return [[round(price * (1 + pct / 100)), portion] for pct, portion in take_profit]

COST_PCT = 0.25


def simulate_exit(bars: pd.DataFrame, entry: float, stop: float, targets: list, max_hold: int) -> dict:
    """진입 이후 일봉(bars, 진입일 포함)에서 언제 어떻게 나가는지.

    targets = [[가격, 파는 비율], ...] — 예: [[11000, 0.5], [11500, 0.5]] (+10%에 절반, +15%에 나머지)
    exit 는 나눠 판 가격들의 가중평균입니다.
    """
    pending = sorted([list(t) for t in targets])
    remaining, proceeds, hits = 1.0, 0.0, 0
    for i, (day, b) in enumerate(bars.iterrows()):
        if b["저가"] <= stop:  # 같은 날 손절·익절이 겹치면 손절로 봅니다(보수적)
            px = min(stop, b["시가"]) if i > 0 else stop  # 갭하락이면 시가에 손절
            proceeds += remaining * px
            return {"exit_date": day, "exit": proceeds, "result": "손절" if not hits else f"{hits}차 익절 후 손절"}
        while pending and b["고가"] >= pending[0][0]:
            price, portion = pending.pop(0)
            take = min(portion, remaining)
            proceeds += take * (max(price, b["시가"]) if i > 0 else price)
            remaining -= take
            hits += 1
        if remaining <= 1e-9:
            return {"exit_date": day, "exit": proceeds, "result": "익절 완료"}
        if i + 1 >= max_hold:
            proceeds += remaining * b["종가"]
            return {"exit_date": day, "exit": proceeds, "result": "기간만료" if not hits else f"{hits}차 익절 후 기간만료"}
    return {"exit_date": None, "exit": None, "result": "진행중"}


def run(df: pd.DataFrame, stop_loss_pct: float, take_profit: list, max_hold: int = 20) -> list[dict]:
    f = features(df)
    trades, busy_until = [], None
    for i in range(len(df) - 1):
        day = df.index[i]
        if busy_until is not None and day <= busy_until:
            continue  # 한 종목에 한 번에 한 포지션
        for s in detect(f.iloc[i]):
            entry = float(df["시가"].iloc[i + 1])
            stop = max(s["stop"], entry * (1 - stop_loss_pct / 100))  # 셋업 손절과 -N% 중 가까운 쪽
            if entry <= stop:
                continue
            out = simulate_exit(df.iloc[i + 1:], entry, stop, targets_for(entry, take_profit), max_hold)
            if out["result"] == "진행중":
                continue
            ret = (out["exit"] / entry - 1) * 100 - COST_PCT
            trades.append({"setup": s["setup"], "date": day, "entry": entry, "ret_pct": ret,
                           "r": (out["exit"] - entry) / (entry - stop), **out})
            busy_until = out["exit_date"]
            break
    return trades


def stats(trades: list[dict], risk_pct: float = 1.0) -> dict:
    if not trades:
        return {"trades": 0}
    wins = [t["ret_pct"] for t in trades if t["ret_pct"] > 0]
    losses = [-t["ret_pct"] for t in trades if t["ret_pct"] <= 0]
    equity, peak, mdd = 1.0, 1.0, 0.0
    for t in sorted(trades, key=lambda t: t["date"]):
        equity *= 1 + risk_pct / 100 * t["r"]
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "payoff": round(avg_win / avg_loss, 2) if avg_loss else None,  # 평균 이익 ÷ 평균 손실
        "avg_ret_pct": round(sum(t["ret_pct"] for t in trades) / len(trades), 2),
        "total_return_pct": round((equity - 1) * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "reliable": len(trades) >= 20,  # 20번 미만이면 우연일 가능성이 큽니다
    }


def by_setup(trades: list[dict], risk_pct: float = 1.0) -> dict:
    return {name: stats([t for t in trades if t["setup"] == name], risk_pct)
            for name in sorted({t["setup"] for t in trades})}


def by_setup_regime(trades: list[dict], regimes: "pd.Series | None", risk_pct: float = 1.0) -> dict:
    """셋업 × 시장 국면별 성적. 국면은 신호가 뜬 날(진입 전날)의 코스피 국면 — 그날까지의 데이터로만 판정."""
    if regimes is None or regimes.empty:
        return {}
    labels = regimes.dropna()
    out = {}
    for name in sorted({t["setup"] for t in trades}):
        rows = {}
        for t in trades:
            if t["setup"] != name:
                continue
            pos = labels.index.searchsorted(t["date"], side="right") - 1
            if pos < 0:
                continue
            rows.setdefault(labels.iloc[pos], []).append(t)
        out[name] = {rg: stats(ts, risk_pct) for rg, ts in sorted(rows.items())}
    return out


def run_candles(df: pd.DataFrame, stop_loss_pct: float, take_profit: list, max_hold: int = 20) -> list[dict]:
    """상승 반전 캔들이 나온 다음 날 시가에 샀다면? 손절은 신호 캔들 저가와 -N% 중 가까운 쪽."""
    from .candles import BULLISH, patterns
    p = patterns(df)
    trades, busy_until = [], None
    for i in range(len(df) - 1):
        day = df.index[i]
        if busy_until is not None and day <= busy_until:
            continue
        hits = [n for n in BULLISH if p[n].iloc[i]]
        if not hits:
            continue
        entry = float(df["시가"].iloc[i + 1])
        stop = max(float(df["저가"].iloc[i]), entry * (1 - stop_loss_pct / 100))
        if entry <= stop:
            continue
        out = simulate_exit(df.iloc[i + 1:], entry, stop, targets_for(entry, take_profit), max_hold)
        if out["result"] == "진행중":
            continue
        ret = (out["exit"] / entry - 1) * 100 - COST_PCT
        trades.append({"setup": hits[0], "date": day, "entry": entry, "ret_pct": ret,
                       "r": (out["exit"] - entry) / (entry - stop), **out})
        busy_until = out["exit_date"]
    return trades
