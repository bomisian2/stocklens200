# Market Regime Read Model v1

이 모델은 개인 로컬 스냅샷만 읽는 결정론적 연구 모델이다. 별도 수집기가 키 없는 공식 공개 다운로드를 1회씩 호출해 원문과 정규화 결과를 `private_data`에만 저장한다. 정식 적정가, 매수구간, 운영 신호를 만들지 않으며 모든 결과는 `operational_eligible=false`다.

## 입력 계약

관측치마다 아래 필드가 필요하다.

- `metric_id`, `value`, `unit`
- `source`, `source_hash`
- `as_of`, `available_at`, `frequency`, `status`

`available_at`이 분석 시각보다 늦은 자료는 미래정보로 차단한다. 지표별 허용 지연을 넘은 자료와 `stale`, `unavailable`, `blocked` 상태는 계산에서 제외한다. 결측치는 0으로 대체하지 않는다.

## 분류 축과 규칙

임계값과 가중치는 `stocklens/personal/market_regime.py`의 `METRIC_RULES`가 단일 원천이다.

- 경기: 정책금리, 2년·10년 금리, 2s10s 스프레드, 실업률 변화, 고용 증가, QQQ 63거래일 추세 프록시
- 물가: CPI·PCE 전년비의 3개월 변화, 연준 명목 광의 달러지수 63관측 변화
- 리스크: VIX, HY OAS, 연준 명목 광의 달러지수, QQQ 추세·20일 연율 변동성·관측구간 낙폭

각 규칙은 `-1`, `0`, `+1`로 평가한 후 가중평균한다. 점수가 `+0.35` 이상이면 유리, `-0.35` 이하면 불리, 그 사이는 중립·혼재다. 커버리지는 사용한 가중치/전체 기대 가중치다. 80% 이상은 high, 60% 이상은 medium, 그 미만은 low 신뢰도로 표시한다.

## QQQ 로컬 프록시

거시 스냅샷이 없으면 해시가 일치하는 로컬 QQQ 원시·비조정 EOD 캐시에서 다음만 계산한다.

- 63거래일 가격 추세
- 최근 20일 로그수익률의 연율 실현변동성
- 로컬 캐시 관측구간 최고 종가 대비 낙폭

이는 경기·물가 전체를 대표하지 않는다. 전체 커버리지가 60% 미만이면 상태는 `proxy_low_confidence`이며 국면 조정 PER 밴드는 P35~P50을 그대로 유지한다.

## Official Macro Snapshot Collector v1

`python scripts/collect_official_macro.py`는 인증정보 없이 다음 읽기 전용 원천을 한 번씩 요청한다.

- FRED graph CSV: DFF, DGS2, DGS10, CPIAUCSL, PCEPI, UNRATE, PAYEMS, BAMLH0A0HYM2, DTWEXBGS
- Cboe 공식 VIX daily history CSV
- 기존 로컬 QQQ 원시·비조정 캐시는 해시가 일치할 때만 결합

FRED/Cboe의 실제 release timestamp를 원문에서 확보할 수 없으므로 현재 스냅샷의 `available_at`은 `collected_at`을 사용하고 `availability_status=current_snapshot_proxy`로 표시한다. 이는 현재 화면용 스냅샷이며 과거 백테스트용 진짜 vintage/PIT 자료가 아니다. DXY 대신 연준 명목 광의 달러지수를 쓰며 DXY로 표기하지 않는다.

원문 CSV, SHA-256, 정규화 스냅샷은 Git에서 제외된 `private_data/macro/`에 저장한다. 원문은 SHA-256 기반 불변 파일명으로 저장하고, 두 공식 응답의 파싱·단위·중복·시점·커버리지 검증이 모두 끝난 뒤에만 스냅샷을 원자적으로 교체한다. 실패하면 이전 스냅샷과 그 원문을 보존하며, 이후 로드 시 지표별 freshness 규칙을 넘으면 `stale`로 제외한다. 결측값은 0으로 채우지 않는다.

FRED 이용 고지: "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." Cboe VIX 및 FRED가 호스팅하는 제3자 계열은 개인 로컬 연구 범위에서만 사용하며 공개 저장소에 원문을 재배포하지 않는다.

## 자기 역사 PER 국면 후보

기존 v23 자기 역사 PER 결과는 수정하지 않는다. 별도 `regime_adjusted_candidate`가 다음 밴드만 선택한다.

- 유리: 경기 확장 + 물가 안정·둔화 + 리스크온, P50~P70
- 중립·혼재: P35~P50
- 불리: 경기 둔화 + 리스크오프, P25~P35
- 커버리지 60% 미만: P35~P50, `proxy_low_confidence`

가격 후보는 현재 v23 TTM 희석 EPS × 선택된 정제 역사 PER 분위수다. DCF, 피어 배수, StockLens EPS/PER, 목표가격, 매수 신호에는 연결하지 않는다.

## 현재 로컬 데이터 한계

공식 수집 전에는 저장소 외부의 QQQ 100거래일 프록시만 사용한다. 실제 공식 스냅샷이 없으면 화면은 낮은 커버리지와 누락 입력을 공개하며 fixture의 유리·중립·불리 결과를 실제 시장 판단으로 표시하지 않는다.
