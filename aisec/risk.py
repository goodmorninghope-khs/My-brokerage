"""리스크관리부: 5가지 기준을 모두 통과해야 '승인 요청'으로 올라갑니다. 하나라도 걸리면 차단.

AI가 아니라 규칙이 판단합니다. 기준 숫자는 config.yaml 의 risk 항목.
"""
import math


def gate(plan: dict, atr_pct: float, market: dict, risk: dict, exposure: float) -> dict:
    capital = risk["capital"]
    per_share_loss = plan["entry"] - plan["stop"]
    budget = capital * risk["risk_per_trade_pct"] / 100          # 한 번에 잃어도 되는 돈
    shares = math.floor(budget / per_share_loss)
    shares = min(shares, math.floor(capital * risk["max_position_pct"] / 100 / plan["entry"]))
    position = shares * plan["entry"]
    loss_at_stop = shares * per_share_loss
    total_exposure_pct = (exposure + position) / capital * 100

    checks = [
        ("01 투자비중", shares > 0,
         f"{shares}주 · {position:,.0f}원 (자산의 {position / capital * 100:.1f}%, 한도 {risk['max_position_pct']}%)"),
        ("02 총노출", total_exposure_pct <= risk["max_total_exposure_pct"],
         f"매수 후 주식 비중 {total_exposure_pct:.1f}% (한도 {risk['max_total_exposure_pct']}%)"),
        ("03 손익비", plan["rr"] >= risk["min_rr"], f"1 : {plan['rr']} (최소 1 : {risk['min_rr']})"),
        ("04 최대손실", -plan["stop_pct"] <= risk["stop_loss_pct"] and loss_at_stop <= budget,
         f"손절 시 -{loss_at_stop:,.0f}원, 손절폭 {plan['stop_pct']}% (한도 -{risk['stop_loss_pct']}%)"),
        ("05 변동성", atr_pct <= risk["max_atr_pct"] and not market.get("shock", False),
         f"하루 평균 변동폭 {atr_pct:.1f}% (한도 {risk['max_atr_pct']}%), 시장 급변 {'있음' if market.get('shock') else '없음'}"),
    ]
    pause = risk.get("pause_until")
    if pause:
        from .data import today_kst
        resting = today_kst() <= (pause if hasattr(pause, "year") else __import__("datetime").date.fromisoformat(str(pause)))
        checks.append(("06 휴식", not resting, f"{pause}까지 실전 신규 매수 쉬기 (모의투자만)" if resting else "휴식 기간 아님"))
    return {
        "verdict": "통과 → 승인 요청" if all(ok for _, ok, _ in checks) else "차단 → 매매 보류",
        "shares": shares,
        "position": position,
        "loss_at_stop": loss_at_stop,
        "checks": [{"name": n, "pass": bool(ok), "detail": d} for n, ok, d in checks],
    }
