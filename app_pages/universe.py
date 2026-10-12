from __future__ import annotations

import streamlit as st

from stocklens.analysis import STATE_LABELS, catalog_table
from stocklens.mvp_context import context
from stocklens.ui import render_market_alert


catalog, status, _ = context()
table = catalog_table(catalog)

st.title("StockLens 200")
st.caption("미국 시총 상위 200개 기업 · 검증된 영역은 표시하고 부족한 영역은 보류 사유를 남깁니다.")
render_market_alert(status)

metrics = st.columns(4, border=True)
metrics[0].metric("표시 기업", f"{len(table):,}개")
metrics[1].metric("시장 상태", str(status.get("data_state") or "확인 불가"))
metrics[2].metric("시장 기준일", str(status.get("market_session_date") or "확인 불가"))
metrics[3].metric("정식 적정가", "0개")

query_col, sector_col, sort_col = st.columns([2, 1, 1])
query = query_col.text_input("기업 검색", placeholder="티커 또는 기업명", key="universe_query")
sectors = ["전체", *sorted(value for value in table["sector"].dropna().unique() if value != "Unknown")]
sector = sector_col.selectbox("섹터", sectors, key="universe_sector")
sort = sort_col.selectbox("정렬", ["시총 순위", "기업명", "가격 높은 순"], key="universe_sort")
state_options = ["전체", *STATE_LABELS]
state_filter = st.selectbox("분석 상태 필터", state_options, format_func=lambda value: "전체" if value == "전체" else STATE_LABELS[value], key="universe_state")
view_mode = st.segmented_control("표시 항목", ["핵심", "분석 상태"], default="핵심", key="universe_view_mode")

view = table.copy()
if query:
    mask = view["ticker"].str.contains(query, case=False, na=False) | view["company_name"].str.contains(query, case=False, na=False)
    view = view[mask]
if sector != "전체":
    view = view[view["sector"] == sector]
if state_filter != "전체":
    state_columns = ["business_state", "financial_state", "valuation_state", "technical_state"]
    view = view[view[state_columns].eq(state_filter).any(axis=1)]
if sort == "기업명":
    view = view.sort_values("company_name")
elif sort == "가격 높은 순":
    view = view.sort_values("price", ascending=False)
else:
    view = view.sort_values("rank")

if view_mode == "분석 상태":
    display = view[["ticker", "business_state", "financial_state", "valuation_state", "technical_state"]].copy()
    for column in display.columns[1:]:
        display[column] = display[column].map(STATE_LABELS)
    display = display.rename(columns={"ticker": "티커", "business_state": "사업", "financial_state": "재무", "valuation_state": "가치", "technical_state": "기술"})
else:
    display = view[["rank", "ticker", "company_name", "price", "market_date"]].rename(columns={"rank": "순위", "ticker": "티커", "company_name": "기업명", "price": "시장 가격", "market_date": "시장 기준일"})

event = st.dataframe(
    display,
    hide_index=True,
    height=560,
    on_select="rerun",
    selection_mode="single-row",
    key="universe_table",
    column_config={"시장 가격": st.column_config.NumberColumn(format="$%.2f"), "시장 기준일": st.column_config.DateColumn(format="YYYY-MM-DD")},
)
st.caption(f"필터 결과 {len(view):,}개 · 연구 가격으로 정렬하거나 추천 순위를 만들지 않습니다.")

if event.selection.rows:
    selected_index = event.selection.rows[0]
    selected_ticker = str(view.iloc[selected_index]["ticker"])
    st.session_state["selected_ticker"] = selected_ticker
    with st.container(border=True):
        selected = view.iloc[selected_index]
        st.markdown(f"**{selected_ticker} · {selected['company_name']}**")
        st.caption("상단의 ‘종목 상세’ 화면에서 검증 영역과 보류 사유를 확인할 수 있습니다.")

st.warning("정식 적정가와 독립 검증 활성 신호는 현재 0개입니다. 연구 결과는 투자 추천에 사용하지 않습니다.")
