from __future__ import annotations

import streamlit as st

from stocklens.mvp_context import context


_, _, signals = context()
st.warning("단기 신호 연구 · 운영 게시 아님")
st.title("단기 신호 검증")
st.caption("PR #3의 검증 보고를 읽기 전용 요약으로 표시하며 원시 가격 이력은 포함하지 않습니다.")

metrics = st.columns(4, border=True)
metrics[0].metric("가격 이력 확보", f"{signals['history_coverage']['available']}/{signals['history_coverage']['universe']}")
metrics[1].metric("전략·기간 조합", f"{signals['strategy_horizon_combinations']:,}")
metrics[2].metric("독립 검증 통과", f"{signals['independent_validated_count']}개")
metrics[3].metric("현재 활성 신호", f"{signals['active_signal_count']}개")

st.subheader("연구 전략")
st.write(" · ".join(signals["strategies"]))
st.caption("다음 거래일 시가 진입 · 5/10/20거래일 · QQQ 비교 · 기본 왕복비용 0.2% · 스트레스 0.6%")

case = signals["lrcx_case"]
with st.expander("LRCX EMA Crossback 10일 연구 사례", expanded=True):
    st.warning(f"상태: {case['validation_state']} · {case['hold_reason']}")
    sample_columns = st.columns(4, border=True)
    sample_columns[0].metric("전체 표본", case["total_samples"])
    sample_columns[1].metric("개발학습", case["train_samples"])
    sample_columns[2].metric("워크포워드", case["walk_forward_samples"])
    sample_columns[3].metric("최종 독립", case["final_samples"])
    result_columns = st.columns(4, border=True)
    result_columns[0].metric("최종 승률", f"{case['final_win_rate']:.2%}")
    result_columns[1].metric("평균 수익률", f"{case['final_average_return']:.2%}")
    result_columns[2].metric("QQQ 초과수익", f"{case['final_average_excess_return']:.2%}")
    result_columns[3].metric("MTM 최대낙폭", f"{case['final_mtm_max_drawdown']:.2%}")
    st.write(f"평균 MAE {case['final_average_mae']:.2%} · 평균 MFE {case['final_average_mfe']:.2%} · 최악 진입 갭 {case['final_worst_entry_gap']:.2%}")
    st.write(f"0.6% 비용 스트레스 평균수익 {case['final_stressed_cost_average_return']:.2%}")

with st.expander("데이터 편향과 이용권한 제한"):
    for limitation in signals["limitations"]:
        st.write(f"- {limitation}")

st.link_button("단기 신호 Draft PR #3 열기", signals["source_pr"], icon=":material/open_in_new:")
st.error("독립 검증 통과 전략과 현재 활성 신호는 모두 0개입니다. 매수 추천을 생성하지 않습니다.")
