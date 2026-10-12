# StockLens 200 v2

미국 거래소 상장기업 전체에서 시가총액 상위 200개 **기업**을 매일 선정하고, 저장된 결과만 빠르게 보여 주는 Streamlit 대시보드입니다.

## 현재 완료 범위

- Yahoo 거래소 스크리너를 1차 공급원으로 사용합니다.
- Nasdaq 미국 상장증권 전체 스크리너를 독립 시총·구성 검증 및 섹터·국가 보강 소스로 사용합니다.
- ETF, ETN, 펀드, 워런트, 권리, 유닛, 우선주, 채권을 제외합니다.
- Alphabet처럼 복수 주식 클래스가 있는 회사는 거래량이 큰 대표 티커 하나만 유지합니다.
- Yahoo와 Nasdaq Top 200의 종목 중첩률 및 공통 종목 시총 차이를 검증한 결과만 저장합니다.
- 공급원 또는 품질 검증이 실패하면 검증된 마지막 정상 `data/universe.csv`를 사용합니다.
- 403, 429, 5xx와 네트워크 오류는 지수 백오프로 최대 3회 재시도합니다.
- yfinance 호출은 60초 제한 subprocess에서 실행해 전체 작업이 무기한 멈추지 않게 합니다.
- Streamlit은 외부 API를 호출하지 않고 저장 스냅샷만 읽습니다.
- `exchange-calendars`의 XNYS 캘린더로 휴장일과 EDT/EST 정규장 마감 시각을 판정합니다.
- GitHub Actions는 PR에서 읽기 권한으로 테스트하고, 예약 작업에서만 쓰기 권한으로 데이터를 갱신합니다.

23:15 UTC는 미국 동부 서머타임(EDT)에는 19:15, 표준시(EST)에는 18:15이므로 두 경우 모두 16:00 정규장 마감 이후입니다. 예약 작업 자체는 평일 휴장일에도 시작할 수 있지만, XNYS 캘린더가 최근 완료 거래 세션을 계산합니다. 거래 시각이 그 세션과 일치하지 않으면 `stale`, 시각을 검증할 수 없으면 `unverified`, 일부 시각·품질 문제가 있으면 `degraded`로 분류하고 신규 스냅샷을 게시하지 않습니다.

Nasdaq 무료 스크리너는 현재 신뢰할 수 있는 종목별 거래 시각을 제공하지 않으므로 수집 실행 시각을 거래 시각으로 대체하지 않습니다. 따라서 Nasdaq 결과만으로 최신 스냅샷을 덮어쓰지 않으며, Yahoo 결과의 독립 검증과 메타데이터 보강에 사용합니다.

`engine.py`의 기존 적정가/신호 코드는 프로토타입 참고용이며 v2 앱에 연결하지 않습니다. 2단계 적정가는 `stocklens/valuation.py`의 버전 고정 모델만 사용하고, 3단계 신호는 QQQ 벤치마크와 워크포워드 검증이 끝나기 전까지 정상 기능으로 표시하지 않습니다.

### v2 2단계 Draft: 자동 적정가 엔진

- 공식 SEC EDGAR `companyfacts`와 `submissions` 파일에서 최근 5개 연도의 GAAP 재무와 실제 이용 가능 시각을 수집합니다.
- 공식 U.S. Treasury 10년 금리를 시장 기준일 이전의 할인율 입력으로 사용합니다.
- 정규화 EPS×PER, FCF DCF, EV/EBITDA 및 금융업 전용 residual P/B 중 입력 게이트를 통과한 모델만 자동 선택합니다.
- 모델 버전, 원천 URL, 제출일, 접수시각, 보수/기본/낙관 값, 자동 안전마진, 신뢰도와 보류 사유를 저장합니다.
- 현재 주가는 적정가 계산식에서 제외하고 계산이 끝난 뒤 저평가율과 판단에만 사용합니다.
- REIT의 검증된 FFO/AFFO, IFRS ADR, 재배포 가능한 분석가 컨센서스가 없으면 정상 값으로 대체하지 않고 판단을 보류합니다.
- 앱은 `data/valuations.csv`와 이력 파일만 읽으며 SEC나 Treasury에 직접 접속하지 않습니다.
- SEC API 403은 재시도하지 않으며 HTTP 상태·도메인·DNS/TLS/timeout·허용된 응답 헤더만 진단합니다. 응답 본문과 쿠키는 로그에 남기지 않습니다.
- 기본 경로는 사용자가 합법적으로 내려받은 공식 SEC 개별 JSON 또는 벌크 ZIP을 SHA-256 manifest로 검증하는 오프라인 import입니다. 직접 API는 명시적 `--financial-source sec-api` 선택 때만 사용하며 403 뒤 자동 재시도·우회·벌크 전환을 하지 않습니다.
- 원본 파일 해시·출처 URL·획득 시각과 정규화 결과 해시·버전을 기록하며, `fixture=false`가 아닌 manifest는 운영 입력으로 거부합니다.
- acceptance timestamp가 없으면 filed 날짜 당일 00:00으로 간주하지 않고 다음 UTC 날짜 23:59:59까지 보수적으로 지연하며 추정 시각임을 저장합니다.
- 과거 재무 캐시는 stale로 표시하고 신규 매매 신호에 사용할 수 없게 하며, 전체 공급자 장애 시 마지막 정상 CSV를 덮어쓰지 않습니다.

설계 결정과 데이터 이용 범위는 [`docs/adr/0002-point-in-time-valuation-engine.md`](docs/adr/0002-point-in-time-valuation-engine.md), 오프라인 절차는 [`docs/sec-offline-import.md`](docs/sec-offline-import.md), 공급원 약관 검토는 [`docs/financial-source-review.md`](docs/financial-source-review.md), 자동 수집 대안은 [`docs/automatic-financial-ingestion.md`](docs/automatic-financial-ingestion.md)에 기록했습니다.

## 로컬 실행

```bash
python -m pip install -r requirements.txt
python scripts/update_data.py
python scripts/update_valuations.py --financial-source offline --sec-manifest data/sec-import/manifest.json
streamlit run app.py
```

테스트:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/validate_snapshot.py --strict
python scripts/validate_valuations.py
```

## 데이터 파일

- `data/universe.csv`: 순위, 티커, 기업명, 섹터/국가, 시가총액, 최근가, 거래 시각, 별도 수집 시각, 데이터·메타데이터 공급원
- `data/status.json`: 거래 세션, 시각 범위와 중앙값, 수집 시각, `healthy/degraded/stale/unverified`, 캐시 여부, 품질·교차검증 결과
- `data/financials.csv`: SEC 제출 재무, 회계연도·양식·접수번호·시장 이용 가능 시각과 검증 상태
- `data/valuations.csv`: 기업별 시나리오 적정가, 매수 유리 구간, 신뢰도, 판단과 모델 근거
- `data/valuation_history.csv`: 날짜별 적정가 변화 이력
- `data/valuation_status.json`: 모델 버전, 평가/보류 수, 공식 금리 입력, 공급원 오류와 알려진 제약
- `data/financial_provenance.json`: 공식 SEC 원본 파일 SHA-256, 출처·획득시각, 정규화 버전과 결과 SHA-256

스냅샷 저장은 임시 파일 작성 후 교체하는 원자적 방식입니다. 200개 미만, 중복 기업, 비정상 순위, 음수/누락 시가총액, 가격·거래량 커버리지 미달, 거래·수집 시각 오류, 독립 검증 실패, 섹터·국가 커버리지 80% 미만이면 새 스냅샷으로 인정하지 않습니다. 실패 시 CSV를 덮어쓰지 않고 상태만 갱신하며 `eligible_for_signals=false`로 기록합니다.

## 2026-10-11 실제 수집 검증

- Yahoo 후보 상장증권 7,328개에서 200개 기업 선정
- 시장 세션: 2026-10-09, 200개 거래 시각 모두 XNYS 최근 완료 세션과 일치
- Nasdaq 독립 Top 200과 193개 일치(96.5%)
- 공통 193개 중 192개의 시가총액 차이가 35% 이내(99.48%)
- Nasdaq 검증 응답의 종목별 거래 시각은 `unverified`로 별도 기록하고 최신성 판정에는 사용하지 않음
- 가격·시총·거래량·거래 시각 커버리지 100%
- 섹터 96.0%, 국가 96.5% 커버리지
- 티커 및 발행사 중복 0건
- 단위·파이프라인·시장 캘린더·Streamlit 스모크 테스트 19개 통과

이는 무료 공급원 두 곳의 내부·상호 일관성 검증이며 거래소의 공식 상장 원장이나 유료 실시간 피드와 동일함을 보증하지 않습니다.

## ADR 및 복수 클래스 정책

미국 거래소 상장 기준이므로 TSM·ASML·BABA·NVO 같은 ADR도 포함될 수 있습니다. `issuer_key`는 회사명에서 `ADR/ADS`, 법인 접미사, `Class A/C`, `Common Stock` 등을 제거해 발행사 후보를 묶고, 동일 발행사로 판정된 복수 클래스에서는 거래량이 큰 한 종목만 유지합니다. 예를 들어 GOOG/GOOGL 중 해당 수집일 거래량이 큰 티커 하나만 남깁니다. 이름 기반 식별은 휴리스틱이며 향후 SEC CIK 매핑으로 보강할 예정입니다.

## 무료 운영 구조

```text
Yahoo 거래소 시장 ── 거래 시각/Top 200 ─┐
                                         ├─ 교차검증/메타데이터 → 품질 게이트 → CSV/JSON
Nasdaq 전체 시장 ─── 독립 시총/구성 ───┘                                  │
                         검증 실패 시 ← 마지막 정상 캐시                  ▼
                                                                  Streamlit 읽기 전용 표시
```

하루 한 번 15분 제한의 단일 예약 작업과 작은 CSV/JSON만 사용합니다. PR CI는 `contents: read`, 예약 갱신 job만 `contents: write`입니다. API 키가 필요 없으며 `.streamlit/secrets.toml`은 Git에서 제외됩니다. 향후 키가 필요한 공급원을 추가할 때는 GitHub Actions Secrets와 Streamlit Secrets만 사용하고 값이나 응답 본문을 로그에 기록하지 않아야 합니다.

## 다음 단계(아직 미완료)

1. 재배포 가능한 독립 재무 공급원을 확보해 SEC 재무를 교차검증
2. 무료·point-in-time 분석가 컨센서스 및 기업 가이던스 출처 확보
3. IFRS ADR 태그와 REIT FFO/AFFO 전용 정규화
4. 가격 이력의 point-in-time 저장과 EMA Crossback, 돌파, 눌림목, RSI 회복 워크포워드 검증
5. 5/10/20거래일 승률·평균수익·손익비·최대낙폭·QQQ 초과수익 및 최소 표본 게이트

## 데이터 한계

무료 공급원의 시가총액, 가격, 기업 분류는 지연되거나 정정될 수 있습니다. 미국 거래소 상장 여부와 기업 소재국은 서로 다른 개념이므로 해외 기업의 ADR도 포함될 수 있습니다. 이 프로젝트는 연구용이며 투자 권유가 아닙니다.

Nasdaq 무료 스크리너의 거래 기준시각은 검증용으로 사용할 수 없고, 일부 종목의 섹터·국가가 비어 있어 현재 메타데이터 커버리지는 100%가 아닙니다. 2단계 적정가 엔진은 Draft로 구현됐지만 공식 SEC 원본 파일이 아직 제공되지 않아 GOOGL·AMAT·AVGO·QCOM의 실데이터 평가는 0/4이며 모두 판단 보류입니다. 3단계 단기 신호도 아직 검증되지 않았고, 사용자가 EPS·PER·안전마진을 입력하는 기능은 추가하지 않습니다.
