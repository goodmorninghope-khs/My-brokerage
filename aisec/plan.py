"""운용부: 셋업마다 진입·손절·목표·무효화·손익비를 숫자로 정합니다."""
from .setups import INVALIDATION


def make_plan(close: float, setup: dict, target_rr: float) -> dict:
    stop = setup["stop"]
    risk = close - stop
    target = round(close + target_rr * risk)
    return {
        "setup": setup["setup"],
        "why": setup["why"],
        "entry": round(close),
        "entry_zone": [round(close * 0.995), round(close * 1.005)],  # 다음 날 이 구간 안에서만 진입
        "stop": stop,
        "stop_pct": round(-risk / close * 100, 2),
        "target": target,
        "target_pct": round((target / close - 1) * 100, 2),
        "rr": round((target - close) / risk, 2),
        "invalidation": INVALIDATION[setup["setup"]],
    }
