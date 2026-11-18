from __future__ import annotations

import streamlit as st


st.set_page_config(
    page_title="StockLens 200",
    page_icon=":material/candlestick_chart:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

pages = {
    "투자 분석": [
        st.Page("app_pages/universe.py", title="전체 종목", icon=":material/table_view:", default=True),
        st.Page("app_pages/company.py", title="종목 상세", icon=":material/domain:"),
    ],
    "연구": [
        st.Page("app_pages/research.py", title="GOOGL 연구", icon=":material/science:"),
        st.Page("app_pages/signals.py", title="신호 검증", icon=":material/monitoring:"),
    ],
    "시스템": [
        st.Page("app_pages/data_status.py", title="데이터 상태", icon=":material/database:"),
    ],
}

with st.sidebar:
    st.caption("저장된 검증 스냅샷만 읽습니다.")
    st.caption("평가 파라미터를 사용자가 조정하지 않습니다.")

navigation = st.navigation(pages, position="top")
navigation.run()
