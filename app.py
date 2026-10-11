import time
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from engine import calc_indicators,train_test_signals,valuation

st.set_page_config(page_title='StockLens 200',page_icon='📈',layout='wide')
st.markdown('''<style>.block-container{padding-top:1.5rem}div[data-testid="stMetric"]{background:#182b39;border-radius:12px;padding:14px}</style>''',unsafe_allow_html=True)
st.title('StockLens 200')
st.caption('독립형 미국 대형주 가치평가 · 단기 신호 검증 대시보드 | 데이터가 없으면 추정하지 않습니다')

# Offline candidate universe: NOT verified current market-cap top 200.
# Data providers may block scraping; keep the interface usable while clearly labeling limitations.
FALLBACK_TICKERS = """NVDA AAPL MSFT GOOGL GOOG AMZN META AVGO TSLA BRK-B LLY JPM WMT V XOM MA ORCL COST HD PG UNH NFLX CRM ABBV BAC KO PLTR AMD GE MU AMAT QCOM LRCX KLAC ADI TXN INTC IBM NOW ANET PANW CRWD UBER ADBE INTU ISRG BKNG PEP MCD CAT GS MS AXP SPGI GEV ETN CEG VRT TSM ASML NVO SAP TM NVS AZN SHEL BABA PDD NEE LIN ABT MRK TMO DHR ACN CSCO DIS VZ CMCSA PFE T AMGN GILD MDT SYK BSX ISRG ELV CVS HUM CI REGN VRTX ZTS HCA BMY SBUX LOW TJX ROST NKE DE HON RTX LMT BA GEHC UPS FDX UNP CSX NSC WM RSG ECL SHW APD DOW FCX NEM OXY COP SLB EOG MPC PSX VLO KMI WMB OKE SO DUK AEP EXC SRE D AEE ED PEG PCG EQIX DLR AMT CCI PSA O WELL SPG CB PGR TRV ALL AIG MET PRU AFL MMC AJG AON ICE CME MCO MSCI BLK BX KKR SCHW C USB PNC TFC COF DFS BK STT FITB HBAN KEY RF NTRS FIS FISV PYPL SQ SHOP SNOW DDOG NET MDB TEAM ZS FTNT OKTA HUBS APP ARM MRVL ON MCHP NXPI MPWR CDNS SNPS ANSS TER KEYS STX WDC LITE COHR VST PWR HUBB FIX EME BE""".split()
FALLBACK_TICKERS=list(dict.fromkeys(FALLBACK_TICKERS))

@st.cache_data(ttl=86400,show_spinner=False)
def constituents():
    url='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    try:
        table=pd.read_html(url)[0]
        table['Symbol']=table['Symbol'].astype(str).str.replace('.','-',regex=False)
        data=table[['Symbol','Security','GICS Sector']].rename(columns={'Symbol':'티커','Security':'기업명','GICS Sector':'섹터'})
        return data,False
    except Exception:
        data=pd.DataFrame({'티커':FALLBACK_TICKERS,'기업명':FALLBACK_TICKERS,'섹터':['미확인']*len(FALLBACK_TICKERS)})
        return data,True

@st.cache_data(ttl=43200,show_spinner=False)
def market_caps(tickers):
    rows=[]
    progress=st.progress(0,text='시총 순위 계산 중 (첫 회 시간이 걸립니다)')
    for i,ticker in enumerate(tickers):
        try:
            t=yf.Ticker(ticker)
            fi=t.fast_info
            cap=fi.market_cap
            if cap and np.isfinite(cap):rows.append((ticker,float(cap)))
        except Exception:pass
        if i%10==0:progress.progress((i+1)/len(tickers),text=f'시총 수집 {i+1}/{len(tickers)}')
    progress.empty()
    return pd.DataFrame(rows,columns=['티커','시가총액']).sort_values('시가총액',ascending=False)

@st.cache_data(ttl=1800,show_spinner=False)
def history(ticker,period='5y'):
    d=yf.Ticker(ticker).history(period=period,auto_adjust=True)
    if d.empty:return pd.DataFrame()
    return d.rename(columns={'Open':'Open','High':'High','Low':'Low','Close':'Close','Volume':'Volume'})

@st.cache_data(ttl=21600,show_spinner=False)
def fundamentals(ticker):
    return yf.Ticker(ticker).info

@st.cache_data(ttl=1800,show_spinner=False)
def latest_prices(tickers):
    rows=[]
    # Yahoo batches limit size; avoid misleading real-time claim
    for start in range(0,len(tickers),35):
        batch=list(tickers[start:start+35]); 
        try:
            h=yf.download(batch,period='7d',interval='1d',progress=False,auto_adjust=True,threads=True)
            if h.empty:continue
            close=h['Close']
            if isinstance(close,pd.Series):close=close.to_frame(name=batch[0])
            for t in batch:
                if t not in close.columns:continue
                s=close[t].dropna()
                if len(s):rows.append({'티커':t,'현재가':float(s.iloc[-1]),'등락률':float(s.iloc[-1]/s.iloc[-2]-1) if len(s)>1 else np.nan,'시세기준일':str(s.index[-1].date())})
        except Exception:continue
    return pd.DataFrame(rows)

if 'selected' not in st.session_state:st.session_state.selected='NVDA'
with st.sidebar:
    st.header('데이터 및 업데이트')
    st.caption('가격 데이터: 최대 30분 캐시 · 재무정보: 최대 6시간 캐시 · 구성 종목: 12시간 캐시')
    if st.button('새로 수집 / 캐시 초기화',use_container_width=True):
        st.cache_data.clear();st.rerun()
    st.warning('Yahoo 공개 API는 요청 제한이 있습니다. 현재가는 실시간 체결가가 아닙니다.')
    quick=st.text_input('관심 티커 직접 검색','',placeholder='GOOGL, AVGO, AMAT ...').upper().strip()
    st.divider()
    st.caption('적정가 산출값은 자동 추정이며, EPS/PER/안전마진을 사용자가 편집하지 않습니다.')
    st.caption('본 버전은 외부 AI API 미연결: 검증 가능한 계산 규칙 기반 잠정 평가입니다.')

try:
    universe,used_fallback=constituents()
    if used_fallback:st.warning('외부 구성 종목 페이지 접근 제한(403 등): 로컬 임시 후보 목록으로 전환했습니다. **미국 시총 순위 상위 200개라는 뜻이 아닙니다.** 기업명/업종은 미검증 상태입니다.')
except Exception as e:
    st.error(f'종목 후보 목록 읽기 실패: {e}');st.stop()

top_mode=st.radio('종목 구성 방식',['빠른 모드: 미국 대형주 기본 목록','실측 모드: S&P 500 전종목 시총을 조회해 상위 200 선정'],horizontal=True)
if top_mode.startswith('실측') and used_fallback:
    st.warning('실측 순위는 S&P 500 정식 구성 종목 데이터가 있을 때만 실행 가능합니다. 로컬 후보 목록 모드로 전환합니다.')
if top_mode.startswith('실측') and not used_fallback:
    caps=market_caps(tuple(universe['티커']))
    if caps.empty:
        st.error('시가총액 수집에 실패했습니다. 잠시 후 다시 시도해 주세요.');st.stop()
    universe=universe.merge(caps,on='티커',how='inner').sort_values('시가총액',ascending=False).head(200).reset_index(drop=True)
    st.success(f'S&P 500 구성 기업 중 시총을 확인한 {len(caps)}개에서 상위 {len(universe)}개를 선정했습니다.')
else:
    # Explicitly non-ranked, coverage-mode, not misrepresented as market cap Top200
    priority=['NVDA','AAPL','MSFT','GOOGL','GOOG','AMZN','META','AVGO','TSLA','BRK-B','LLY','JPM','WMT','V','XOM','MA','ORCL','COST','HD','PG','UNH','NFLX','CRM','ABBV','BAC','KO','PLTR','AMD','GE','MU','AMAT','QCOM','LRCX','KLAC','ADI','TXN','INTC','IBM','NOW','ANET','PANW','CRWD','UBER','ADBE','INTU','ISRG','BKNG','PEP','MCD','CAT','GS','MS','AXP','SPGI','GEV','ETN','CEG','VRT']
    first=universe[universe['티커'].isin(priority)].copy()
    first['priority']=first['티커'].map({t:i for i,t in enumerate(priority)})
    first=first.sort_values('priority').drop(columns='priority')
    rest=universe[~universe['티커'].isin(priority)]
    universe=pd.concat([first,rest],ignore_index=True).head(200)
    st.info('빠른 모드는 200개 미국 대형주 **예비 목록**입니다. 정확한 시총 순위가 아니므로 실측 모드를 선택해야 합니다.')

prices=latest_prices(tuple(universe['티커']))
if prices.empty:
    st.warning('시세 제공처 연결에 실패했습니다. 후보 종목 표는 표시하지만 적정가 및 단기 신호는 계산하지 않습니다.')
    prices=pd.DataFrame(columns=['티커','현재가','등락률','시세기준일'])
listing=universe.merge(prices,on='티커',how='left')
with st.container():
    a,b,c,d=st.columns(4)
    a.metric('관리 기업',f'{len(universe)}개');b.metric('가격 수집 성공',f'{listing.현재가.notna().sum()}개');c.metric('가격 누락',f'{listing.현재가.isna().sum()}개');d.metric('최신 시세 기준일',listing['시세기준일'].dropna().max() if listing['시세기준일'].notna().any() else '없음')

tab1,tab2,tab3=st.tabs(['200개 종목 · 탐색','AI 가치평가 · 개별분석','단기 매수 신호 · 과거 검증'])
with tab1:
    filter_=st.text_input('기업명/티커 필터','',key='filter')
    sectors=['전체']+sorted(listing['섹터'].dropna().unique())
    s=st.selectbox('업종',sectors)
    show=listing.copy()
    if s!='전체':show=show[show['섹터']==s]
    if filter_:show=show[show['티커'].str.contains(filter_,case=False)|show['기업명'].str.contains(filter_,case=False)]
    dispcols=['티커','기업명','섹터','현재가','등락률','시세기준일']+(['시가총액'] if '시가총액' in show else [])
    st.dataframe(show[dispcols],hide_index=True,use_container_width=True,height=530,column_config={'현재가':st.column_config.NumberColumn(format='$%.2f'),'등락률':st.column_config.NumberColumn(format='%.2f'),'시가총액':st.column_config.NumberColumn(format='%.0f')})
    st.caption('전체 200개 기업의 정밀 재무·신호 계산을 매 화면 요청마다 강제하면 무료 API 요청 제한에 걸립니다. 선택한 기업에 대해 정밀 계산합니다.')
with tab2:
    eligible=listing[listing['현재가'].notna()]['티커'].tolist()
    if not eligible:
        st.warning('가격 데이터가 없어 가치평가를 수행할 수 없습니다. 데이터 제공처 연결을 먼저 확인하세요.')
        st.stop()
    ticker=st.selectbox('가치평가 종목',eligible,index=eligible.index(quick) if quick in eligible else (eligible.index(st.session_state.selected) if st.session_state.selected in eligible else 0),key='val_ticker')
    try:
        info=fundamentals(ticker)
        p=float(listing.loc[listing['티커']==ticker,'현재가'].iloc[0]);result=valuation(info,p)
        x,y,z=st.columns(3);x.metric('현재가',f'${p:,.2f}');y.metric('AI 자동 잠정가',f'${result["base"]:,.2f}' if 'base' in result else '평가 불가');z.metric('평가 상태',result['status'])
        if 'base' in result:
            v1,v2,v3,v4=st.columns(4)
            v1.metric('보수적 가치',f'${result["bear"]:,.2f}')
            v2.metric('기본 가치',f'${result["base"]:,.2f}')
            v3.metric('낙관적 가치',f'${result["bull"]:,.2f}')
            v4.metric('자동 매수 관찰가',f'${result["buy"]:,.2f}')
            if p<=result['buy']:st.success('가치 기준: 관찰 매수가 이내 — 기술적 신호 확인 필요')
            elif p<=result['base']:st.warning('가치 기준: 기본 가치 이내이나 안전마진 부족')
            else:st.info('가치 기준: 기본 가치보다 높음 — 매수 대기 검토')
            st.write('평가 모델:',result['model'])
            st.write('현재 주가의 기본 가치 대비 프리미엄:',f'{result["premium"]:+.1%}')
            if result['eps']:st.write('공개 데이터상 선행 EPS:',result['eps'],'· 자동 PER:',round(result['pe'],1))
        st.warning(result['reason'])
        h=history(ticker)
        if not h.empty:
            ind=calc_indicators(h)
            fig=go.Figure();fig.add_trace(go.Scatter(x=ind.index,y=ind['Close'],name='가격'));fig.add_trace(go.Scatter(x=ind.index,y=ind['ema20'],name='EMA20'));fig.add_trace(go.Scatter(x=ind.index,y=ind['ma200'],name='MA200'));fig.update_layout(height=410,margin=dict(l=0,r=0,t=20,b=0));st.plotly_chart(fig,use_container_width=True)
    except Exception as e:st.error(f'종목 분석 데이터 수집 실패: {e}')
with tab3:
    st.markdown('**검증 대상 전략:** EMA20 재돌파 · RSI 40 회복 · 20일 신고가(거래량 조건) · 상승추세 눌림 반등')
    st.caption('모든 신호는 200일선 상승 필터를 사용하며 신호일 다음 날 종가에 진입, 10거래일 뒤 종가에 청산하는 단순 전략입니다. 왕복 비용 0.2% 차감.')
    t=st.selectbox('검증 종목',eligible,index=eligible.index(quick) if quick in eligible else (eligible.index('AMAT') if 'AMAT' in eligible else 0),key='test_ticker')
    try:
        h=history(t,'5y')
        if len(h)<400:st.warning('5년 일봉 자료가 부족하여 분석할 수 없습니다.')
        else:
            ind=calc_indicators(h);r,champ=train_test_signals(ind)
            display=r.drop(columns='점수').copy()
            for col in ['검증 승률','검증 평균수익','검증 중위수익']:
                display[col]=display[col]*100
            st.dataframe(display,hide_index=True,use_container_width=True,column_config={'검증 승률':st.column_config.NumberColumn(format='%.1f%%'),'검증 평균수익':st.column_config.NumberColumn(format='%+.2f%%'),'검증 중위수익':st.column_config.NumberColumn(format='%+.2f%%')})
            if not champ:st.warning('검증 구간의 거래 표본이 부족합니다. 최고 확률 전략을 선정하지 않습니다.')
            else:
                row=r[r['전략']==champ].iloc[0]
                st.success(f'과거 검증 평균수익 기준 1위: {champ} | 표본 {int(row["검증 건수"])}건 | 수익 거래 비율 {row["검증 승률"]:.1%} | 10거래일 평균 {row["검증 평균수익"]:+.2%}')
                latest=bool(ind[champ].iloc[-1])
                st.metric('최근 확정 일봉에 해당 신호', '발생' if latest else '없음')
                st.caption(f'마지막 일봉 날짜: {ind.index[-1].date()} · 신호가 발생해도 다음 거래일 종가 진입을 가정하므로 실시간 매수 지시는 아닙니다.')
            st.error('주의: 단일 기간 검증 결과를 사용해 전략을 선택한 후 동일 검증 구간의 성과를 제시하므로 최종 성과에는 선택 편향이 있습니다. 실제 운용 전 별도 미사용 기간·워크포워드·시장 대비 초과수익 검증이 필요합니다.')
    except Exception as e:st.error(f'백테스트 실패: {e}')
st.divider()
st.caption('연구용 프로토타입입니다. 실시간 GPT/Claude 호출, 뉴스 감시, 200종목 일괄 적정가 캐시, 푸시 알림 및 유료 컨센서스 연동은 별도 인프라가 필요한 운영 단계 기능입니다. 데이터 제공 조건과 라이선스를 확인하세요.')
