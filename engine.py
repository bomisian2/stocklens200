import numpy as np
import pandas as pd

COST=0.002
HOLD=10

def calc_indicators(df):
    x=df.copy().dropna(subset=['Close'])
    c=x['Close'].astype(float)
    x['ema20']=c.ewm(span=20,adjust=False).mean()
    x['ma50']=c.rolling(50).mean()
    x['ma200']=c.rolling(200).mean()
    delta=c.diff(); up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x['rsi']=100-100/(1+up/down.replace(0,np.nan))
    x['max20prev']=c.shift(1).rolling(20).max()
    x['vol20']=x['Volume'].rolling(20).mean() if 'Volume' in x else np.nan
    x['rsi_prev']=x['rsi'].shift(1)
    trend=(c>x['ma200']) & (x['ma50']>x['ma200'])
    x['EMA 재돌파']=trend & (c>x['ema20']) & (c.shift(1)<=x['ema20'].shift(1))
    x['RSI 회복']=trend & (x['rsi']>40) & (x['rsi_prev']<=40)
    x['20일 신고가 돌파']=trend & (c>x['max20prev']) & (x['Volume']>1.2*x['vol20'])
    x['상승추세 눌림 반등']=trend & (c>x['ema20']) & (c.shift(1)<x['ema20'].shift(1)) & (x['rsi']>45)
    return x

SIGNALS=['EMA 재돌파','RSI 회복','20일 신고가 돌파','상승추세 눌림 반등']

def signal_trades(x, name, end_index=None):
    c=x['Close'].astype(float).to_numpy()
    sig=x[name].fillna(False).to_numpy()
    dates=x.index
    rows=[]; next_allowed=0
    for i in range(201,len(c)-HOLD-1):
        if end_index is not None and i>=end_index: break
        if i<next_allowed or not sig[i]:continue
        entry=i+1; exit_=entry+HOLD
        if end_index is not None and exit_>=end_index:continue
        ret=c[exit_]/c[entry]-1-COST
        rows.append({'date':dates[i], 'entry':dates[entry], 'exit':dates[exit_], 'return':float(ret)})
        next_allowed=exit_+1
    return pd.DataFrame(rows)

def train_test_signals(x):
    # last 30% of history treated as unseen evaluation; parameters fixed a priori
    split=max(280,int(len(x)*.7))
    results=[]
    for name in SIGNALS:
        tr=signal_trades(x,name,end_index=split)
        te=signal_trades(x.iloc[split-220:].copy(),name) if len(x)>split+HOLD+220 else pd.DataFrame()
        # test series starts with indicator warmup already calculated; use date filter instead
        if not te.empty:te=te[te['date']>=x.index[split]]
        train_count=len(tr); test_count=len(te)
        tr_mean=tr['return'].mean() if train_count else np.nan
        te_mean=te['return'].mean() if test_count else np.nan
        win=float((te['return']>0).mean()) if test_count else np.nan
        # no champion without sufficient OOS observations
        eligible=(train_count>=8 and test_count>=5)
        score=float(te_mean)*100 if eligible else -1e9
        results.append({'전략':name,'훈련 건수':train_count,'검증 건수':test_count,'검증 승률':win,'검증 평균수익':te_mean,'검증 중위수익':float(te['return'].median()) if test_count else np.nan,'점수':score,'충분한 표본':eligible})
    results=pd.DataFrame(results).sort_values('점수',ascending=False)
    ok=results[results['충분한 표본']]
    champion=ok.iloc[0]['전략'] if not ok.empty else None
    return results,champion

def _get_num(info,*names):
    for n in names:
        try:
            v=info.get(n)
            if v is not None and np.isfinite(float(v)) and float(v)>0:return float(v)
        except (TypeError,ValueError):pass
    return None

def valuation(info,price):
    '''Deterministic automatic proxy valuation. Does NOT pretend to be verified analyst consensus.'''
    if not price or price<=0:return {'status':'판단 불가','reason':'가격 없음'}
    eps=_get_num(info,'forwardEps')
    earnings_growth=info.get('earningsGrowth')
    rev_growth=info.get('revenueGrowth')
    gross_margin=info.get('operatingMargins')
    debt=_get_num(info,'debtToEquity')
    sector=str(info.get('sector') or '')
    # Explicitly disclosed heuristic; not validated AI fair value nor optimized market PE
    growth=float(earnings_growth) if isinstance(earnings_growth,(float,int)) and np.isfinite(earnings_growth) else float(rev_growth) if isinstance(rev_growth,(int,float)) and np.isfinite(rev_growth) else 0.08
    growth=np.clip(growth,-.15,.3)
    sector_center={'Technology':24,'Communication Services':22,'Consumer Cyclical':20,'Consumer Defensive':20,'Healthcare':21,'Industrials':21,'Financial Services':14,'Energy':13,'Utilities':17,'Real Estate':16,'Basic Materials':16}.get(sector,19)
    quality=2 if isinstance(gross_margin,(int,float)) and gross_margin>.25 else (-2 if isinstance(gross_margin,(int,float)) and gross_margin<.05 else 0)
    leverage=-2 if debt and debt>160 else 0
    pe=float(np.clip(sector_center+(growth-.08)*25+quality+leverage,9,34))
    if eps is not None:
        base=eps*pe
        return {'status':'잠정 추정','model':'선행 EPS × 업종·성장 보정 PER','eps':eps,'pe':pe,'bear':base*.75,'base':base,'bull':base*1.25,'buy':base*.85,'premium':price/base-1,'reason':'무료 데이터/휴리스틱 기반 잠정치; EPS 회계기준·컨센서스 검증 전'}
    fcf=_get_num(info,'freeCashflow'); shares=_get_num(info,'sharesOutstanding')
    if fcf and shares:
        fcfps=fcf/shares
        fcfyield=np.clip(.055-(growth-.08)*.05,.035,.09)
        base=fcfps/fcfyield
        return {'status':'잠정 추정','model':'FCF/주 ÷ 가정 FCF 수익률','eps':None,'pe':None,'bear':base*.7,'base':base,'bull':base*1.3,'buy':base*.85,'premium':price/base-1,'reason':'단년도 FCF와 가정 할인율 사용; 지속가능 FCF 검증 전'}
    return {'status':'판단 불가','reason':'검증 가능한 양(+)의 EPS 또는 FCF 데이터 없음'}
