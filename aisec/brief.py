"""결재 문서 초안: 숫자가 들어가는 칸은 코드가 먼저 채웁니다.

`python -m aisec.cli` 가 data/<날짜>/brief_draft.md 를 만듭니다. 위원장(AI)은 이 초안을 reports/<날짜>.md 로 옮기고
`[[…]]` 로 표시된 칸(부서 의견·회의 결론·한 줄 요약)만 채웁니다. 숫자를 손으로 옮겨 적다 틀리는 일을 막기 위해서입니다.

    python -m aisec.brief check reports/2026-11-12.md   # 안 채운 칸([[…]])이 남았는지 확인
"""
import sys
from datetime import date
from pathlib import Path

WEEKDAYS = "월화수목금토일"
FOOTER = "백테스트는 과거 성적일 뿐 미래 수익을 보장하지 않습니다. 최종 결정과 책임은 결재자에게 있습니다."


def won(v) -> str:
    return f"{round(v):,}"


def pct(v, sign: bool = True) -> str:
    return "-" if v is None else (f"{v:+}%" if sign else f"{v}%")


def stat_row(label: str, st: dict) -> str:
    if not st or not st.get("trades"):
        return f"| {label} | 0 | - | - | - | - |"
    note = "" if st.get("reliable") else " (표본 부족)"
    return (f"| {label} | {st['trades']}{note} | {st['win_rate_pct']}% | {st.get('payoff') or '-'} | "
            f"{pct(st['avg_ret_pct'])} | {st['mdd_pct']}% |")


def market_section(mkt: dict) -> list[str]:
    if mkt.get("regime") == "판정불가":
        return ["## 시장: 국면과 태세", "- **판정불가** — 코스피 지수를 받지 못했습니다. 급변으로 간주해 신규 매수를 차단합니다.", ""]
    lines = ["## 시장: 국면과 태세",
             f"- 국면 **{mkt['regime']}** · 코스피 {mkt['kospi']:,} · 하루 {pct(mkt['change_1d_pct'])} · 5일 {pct(mkt['change_5d_pct'])} · "
             f"급변 규칙 **{'켜짐(신규 매수 차단)' if mkt['shock'] else '꺼짐'}**",
             f"- 태세(코드): {mkt['stance']}"]
    if mkt.get("recent"):
        lines.append("- 최근 종가: " + " → ".join(f"{r['date'][5:]} {r['close']:,}({pct(r['change_pct'])})" for r in mkt["recent"]))
    fit = mkt.get("fit")
    if fit:
        def names(rows):
            return ", ".join(f"{r['setup']}({r['trades']}회 · 승률 {r['win_rate_pct']}% · 평균 {pct(r['avg_ret_pct'])})" for r in rows) or "없음"
        lines += [f"- 이 국면의 3년 성적(코드 분류, 표본 20회 이상만):",
                  f"  - 맞는 셋업(평균 +0.3% 이상): {names(fit['fit'])}",
                  f"  - 우위 미미(0~+0.3%): {names(fit['thin'])}",
                  f"  - 피할 셋업(평균 0% 이하): {names(fit['avoid'])}"]
        if fit["unknown"]:
            lines.append("  - 표본 부족: " + ", ".join(f"{r['setup']}({r['trades']}회)" for r in fit["unknown"]))
    out = mkt.get("shock_outlook") or []
    if out:
        verb = "풀리려면" if mkt["shock"] else "꺼진 채 유지되려면"
        first = out[0]
        if first["possible"]:
            lines.append(f"- 급변 규칙이 내일 아침 {verb}: 오늘 종가 **{first['lower']:,} 초과 ~ {first['upper']:,} 이하** "
                         f"(현재가 대비 {pct(first['lower_pct'])} ~ +{first['upper_pct']}%)")
        else:
            lines.append(f"- 급변 규칙: **오늘 종가가 어떻게 나와도 내일 아침에는 켜져 있습니다.** 하루 ±3% 구간 "
                         f"{first['low_1d']:,}~{first['high_1d']:,} 과 5일 기준 {first['floor_5d']:,} 초과가 겹치지 않습니다")
        for o in out[1:]:
            lines.append(f"  - {o['step']}거래일 뒤 종가는 5일 기준으로 {o['floor_5d']:,} 초과가 필요(기준일 {o['base_date'][5:]}), "
                         "그날 하루 변동도 ±3% 안이어야 함")
    lines += ["- 마켓부: [[시장을 움직인 원인(확인된 것만, 없으면 '확인 못함' 한 줄) · 태세 · 후보별 국면 의견]]", ""]
    return lines


def card(c: dict) -> list[str]:
    p, g, sc = c["plan"], c["risk"], c["score"]
    rs, tt = c.get("rs") or {}, c["trend_template"]
    checks = " · ".join(f"✅ {k['name']}" if k["pass"] else f"❌ {k['name']}({k['detail']})" for k in g["checks"])
    failed = [k["name"] for k in g["checks"] if not k["pass"]]
    candles = ", ".join(f"{x['date'][5:]} {x['pattern']}({x['kind']})" for x in c.get("candles") or []) or "최근 3일 신호 없음"
    flags = ("⭐ 검증된 상승 캔들 " if c.get("candle_bonus") else "") + ("⚠ 하락 캔들 경고" if c.get("candle_warning") else "")
    flow = c.get("foreign_net_5d")
    targets = p["targets"]
    lines = [f"### {p['setup']} 셋업 · {c['name']} ({c['ticker']})   현재가 {won(p['entry'])}원",
             f"**리스크 판정: {g['verdict']}**" + (f" — 걸린 기준: {', '.join(failed)}" if failed else ""), "",
             "| 진입 구간 | 손절가 | 1차 익절 | 2차 익절 | 손익비 | 수량·금액 | 손절 시 손실 |", "|---|---|---|---|---|---|---|",
             f"| {won(p['entry_zone'][0])}~{won(p['entry_zone'][1])} | {won(p['stop'])} ({p['stop_pct']}%) | "
             f"{won(targets[0][0])} ({int(targets[0][1] * 100)}%) | "
             f"{won(targets[1][0]) + f' ({int(targets[1][1] * 100)}%)' if len(targets) > 1 else '-'} | 1 : {p['rr']} | "
             f"{g['shares']}주 · {won(g['position'])}원 | -{won(g['loss_at_stop'])}원 |", "",
             "| 백테스트(3년) | 거래 수 | 승률 | 손익비 | 평균 수익 | 최대낙폭 |", "|---|---|---|---|---|---|",
             stat_row("이 종목·이 셋업", c["backtest_ticker"]),
             stat_row("전 종목·이 셋업", c["backtest_all"]),
             stat_row(f"전 종목·이 셋업·{c['backtest_regime'].get('regime', '')}(지금 국면)", c["backtest_regime"]), "",
             f"- 강도: 종합 점수 **{sc['total']}/100** (" + " · ".join(f"{k} {v}" for k, v in sc["parts"].items()) + ")",
             f"  - 상대강도 {rs.get('rs_rating', '-')}/99 · 20일 코스피 대비 {pct(rs.get('rs_20d'))}p · 추세 템플릿 {tt['passed']}/{tt['total']}"
             + (" (탈락: " + ", ".join(k for k, v in tt["checks"].items() if not v) + ")" if tt["passed"] < tt["total"] else "")
             + f" · 손절 ÷ 하루 변동폭 {p.get('stop_atr_multiple') or '-'}배 · 하루 변동폭 {c['atr_pct']}%",
             f"- 캔들: {candles} {flags}".rstrip(),
             "- 수급: 외국인 5일 순매수 " + ("자료 없음" if flow is None else f"{flow / 1e8:+,.0f}억 원"),
             f"- 리스크 기준: {checks}",
             f"- 무효화 조건: {p['invalidation']}"]
    return lines


def render(ctx: dict) -> str:
    """ctx: summary(screen.json 내용) · picks(후보 전체 dict) · holdings_text · holding_notes · paper_lines · counts."""
    s, mkt = ctx["summary"], ctx["summary"]["market"]
    d = date.fromisoformat(s["date"])
    picks = ctx["picks"]
    rec = s.get("recommend") or []
    hres = ctx.get("holdings")
    lines = [f"# {s['date']}({WEEKDAYS[d.weekday()]}) 아침 브리핑 — 결재 대기", "",
             f"> 기준: {mkt.get('date', '-')} 종가 · {ctx['n_frames']}종목 수집 · 셋업 성립 {ctx['n_candidates']}건 · 회의 상정 {len(picks)}건", "",
             "## 오늘 결재할 것",
             "[[위원장: 아래 코드 요약을 바탕으로 3줄 이내. 가장 급한 것부터, 대표님이 할 행동을 동사로]]"]
    if hres:
        for r in hres["rows"]:
            warn = " ⚠ " + " / ".join(r["warnings"]) if r.get("warnings") else ""
            gap = (f"손절선 {r['stop']:,}원 이탈(종가가 {(r['price'] / r['stop'] - 1) * 100:+.2f}%)" if r["price"] <= r["stop"]
                   else f"손절선 {r['stop']:,}원까지 {pct(r['to_stop_pct'])}")
            lines.append(f"- (코드) 보유 {r['name']}: **{r['action']}** · 손익 {r['pnl']:+,}원({pct(r['pnl_pct'])}) · {gap}{warn}")
    else:
        lines.append("- (코드) 보유 종목 없음(현금 100%)")
    if rec:
        c = next(c for c in picks if c["ticker"] == rec[0]["ticker"])
        lines.append(f"- (코드) 원칙 추천: {c['name']} {c['plan']['setup']} {c['risk']['shares']}주 · {won(c['risk']['position'])}원 "
                     f"(종합 점수 {c['score']['total']}/100)")
    else:
        why = "시장 급변으로 전부 차단" if mkt.get("shock") else ("오늘 성립한 셋업 없음" if not picks else "리스크 기준 통과 후보 없음")
        lines.append(f"- (코드) 원칙 추천: 없음 — {why}")
    lines.append(f"- (코드) 시장: {mkt.get('regime')} · 급변 규칙 {'켜짐' if mkt.get('shock') else '꺼짐'}")
    lines += ["", "## 모의투자 장부", *(ctx.get("paper_lines") or ["- 장부 없음"]), ""]

    if ctx.get("holdings_text"):
        lines += [ctx["holdings_text"].replace("\n- [ ] 제안대로   [ ] 일부만   [ ] 보류   ← 대표님 결재\n", "\n"), ""]
        lines += ["| 부서 | 의견 |", "|---|---|", "| 분석부 | [[한 줄 + 결론(확신도)]] |", "| 뉴스공시부 | [[한 줄 + 결론(확신도)]] |",
                  "| 반대편부 | [[한 줄 + 결론(확신도)]] |", "",
                  "- 위원장 제안: [[규칙 제안을 바꾸지 않고, 실행 방법만]]",
                  "- [ ] 제안대로   [ ] 일부만   [ ] 보류   ← 대표님 결재", ""]
    else:
        lines += ["## 보유 종목 점검", "- 보유 종목 없음", ""]

    lines.append("## 오늘의 원칙 추천")
    if rec:
        c = next(c for c in picks if c["ticker"] == rec[0]["ticker"])
        p, g = c["plan"], c["risk"]
        lines += [f"**{c['name']}({c['ticker']}) {p['setup']}** — 진입 {won(p['entry_zone'][0])}~{won(p['entry_zone'][1])} · 손절 {won(p['stop'])} · "
                  f"익절 {' → '.join(won(t[0]) for t in p['targets'])} · {g['shares']}주 · {won(g['position'])}원 · 손절 시 -{won(g['loss_at_stop'])}원",
                  "- 부서 요약: [[분석부 · 뉴스공시부 · 반대편부 · 마켓부 한 줄씩]]",
                  "- **위원장 제안: [[승인 / 보류 / 거절]]** — [[이유 한 줄, 자세한 것은 결재 카드]]"]
    else:
        lines.append("**오늘은 추천 없음.** " + ("회의 후보가 모두 리스크 기준에 걸렸습니다." if picks else "오늘 성립한 셋업이 없습니다 — 쉬는 것도 매매입니다."))
    lines += ["", "추천은 원칙이 내놓는 답이지 수익 보장이 아닙니다.", ""]

    lines += market_section(mkt)

    passed = [c for c in picks if c["risk"]["verdict"].startswith("통과")]
    blocked = [c for c in picks if c not in passed]
    lines.append("## 결재 카드")
    if not passed:
        lines += ["- 리스크 기준을 통과한 회의 후보가 없습니다.", ""]
    for c in passed:
        lines += card(c)
        lines += ["- 분석부: [[…]]", "- 뉴스공시부: [[…]]", "- 반대편부: [[반론과 질문, 그 질문에 누가 어떻게 답했는지]]",
                  "- 회의에서 갈린 지점: [[이름 붙이기]]", "- 위원장 제안: [[승인 / 보류 / 거절 + 재검토 조건]]",
                  "- [ ] 승인 → 실행   [ ] 보류   [ ] 거절   ← 대표님 결재", ""]
    if blocked:
        lines += ["## 차단된 회의 후보 — 규칙상 자동 거절 (리스크 '차단'은 AI 의견으로 뒤집지 않습니다)",
                  "| 종목 | 셋업 | 현재가 | 진입 구간 | 손절 | 1차/2차 익절 | 수량 | 종합 점수 · 상대강도 · 템플릿 · 손절÷변동폭 | 지금 국면 성적 | 걸린 기준 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for c in blocked:
            p, g, rg = c["plan"], c["risk"], c["backtest_regime"]
            why = "; ".join(f"{k['name']}: {k['detail']}" for k in g["checks"] if not k["pass"])
            perf = f"{rg['trades']}회 · 평균 {pct(rg['avg_ret_pct'])}" if rg.get("trades") else "-"
            lines.append(f"| {c['name']} | {p['setup']} | {won(p['entry'])} | {won(p['entry_zone'][0])}~{won(p['entry_zone'][1])} | {won(p['stop'])} | "
                         f"{' / '.join(won(t[0]) for t in p['targets'])} | {g['shares']}주 | {c['score']['total']} · {(c.get('rs') or {}).get('rs_rating', '-')} · "
                         f"{c['trend_template']['passed']}/{c['trend_template']['total']} · {p.get('stop_atr_multiple') or '-'}배 | {perf} | {why} |")
        lines += ["- [[분석부 한 줄(묶어 본 것이 있으면). 없으면 이 줄을 지움]]", ""]

    lines.append("## 대기 목록 — 이 가격이 오면 매수 신호")
    if s.get("waiting"):
        lines += ["| 종목 | 셋업 | 신호 가격 | 현재가 대비 | 조건 | 이 셋업 3년 성적(전 종목) |", "|---|---|---|---|---|---|"]
        for w in s["waiting"]:
            b = w.get("backtest_all") or {}
            perf = f"{b['trades']}회 · 평균 {pct(b['avg_ret_pct'])}" if b.get("trades") else "-"
            lines.append(f"| {w['name']} | {w['setup']} | {won(w['trigger'])} | {pct(w['gap_pct'])} | {w['condition']} | {perf} |")
    else:
        lines.append("- 신호 가격 근처에 있는 종목이 없습니다.")
    lines.append("")

    lines.append("## 캔들 신호 (기준일)")
    today = [(s["universe"][t]["name"], x) for t, rc in (s.get("candles_today") or {}).items() for x in rc
             if x["date"] == mkt.get("date") and x["kind"] != "중립"]
    valid = set(s.get("validated_candles") or [])
    if today:
        up = [f"{n} {x['pattern']}{'⭐' if x['pattern'] in valid else ''}" for n, x in today if x["kind"] == "상승"]
        down = [f"{n} {x['pattern']}" for n, x in today if x["kind"] == "하락"]
        lines += [f"- 상승: {', '.join(up) or '없음'} (⭐ = 3년 백테스트로 검증된 캔들: {', '.join(sorted(valid)) or '없음'})",
                  f"- ⚠ 하락: {', '.join(down) or '없음'}"]
    else:
        lines.append("- 방향 있는 캔들 신호 없음")
    lines.append("")

    others = s.get("other_candidates") or []
    if others:
        lines += ["## 그 밖의 후보 (회의에 올리지 않음)", "| 종목 | 셋업 | 리스크 판정 |", "|---|---|---|",
                  *[f"| {o['name']} | {o['setup']} | {o['verdict']} |" for o in others], ""]

    lines.append("## 데이터 한계")
    fails = s.get("failures") or {}
    lines += [f"- {k}: {v}" for k, v in fails.items()] or ["- 수집 실패 없음(failures 없음)"]
    lines += ["- [[확인 못한 것은 한 줄로 묶기 · 부서 호출 내역]]", "",
              "## 어제 결정 돌아보기", "[[어제 위원장 제안이 있었다면: 결과가 아니라 '그날 알 수 있던 근거'로 채점. 없으면 이 절을 지움]]", "",
              "## 오늘의 작은 성공", "[[1~3개. 증거가 있는 것만]]", "", "---", FOOTER, ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "check":
        text = Path(argv[1]).read_text(encoding="utf-8")
        left = [ln for ln in text.splitlines() if "[[" in ln]
        for ln in left:
            print(f"안 채운 칸: {ln.strip()[:100]}")
        print("통과: 안 채운 칸이 없습니다." if not left else f"{len(left)}칸이 남았습니다.")
        return 1 if left else 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
