"""운용부: 셋업마다 진입·손절·익절·무효화·손익비를 숫자로 정합니다."""
from .backtest import targets_for
from .setups import INVALIDATION


def make_plan(close: float, setup: dict, stop_loss_pct: float, take_profit: list) -> dict:
    stop = max(setup["stop"], round(close * (1 - stop_loss_pct / 100)))  # 셋업 손절과 -N% 중 가까운 쪽
    risk = close - stop
    targets = targets_for(close, take_profit)
    return {
        "setup": setup["setup"],
        "why": setup["why"],
        "entry": round(close),
        "entry_zone": [round(close * 0.995), round(close * 1.005)],  # 다음 날 이 구간 안에서만 진입
        "stop": stop,
        "stop_pct": round(-risk / close * 100, 2),
        "targets": targets,                      # [[가격, 파는 비율], ...]
        "target": targets[0][0],                 # 1차 익절가
        "target_pct": round((targets[0][0] / close - 1) * 100, 2),
        "rr": round((targets[0][0] - close) / risk, 2),  # 1차 익절 기준 손익비
        "invalidation": INVALIDATION[setup["setup"]],
    }
