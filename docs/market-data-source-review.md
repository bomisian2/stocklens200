# 가격 데이터 공급원 및 이용권한 검토

- 검토일: 2026-10-12
- 범위: 미국 Top 200 일봉 OHLCV 수집, 연구 백테스트, 공개 Streamlit 파생 신호 게시
- 결론: 공개 운영·재배포까지 명시적으로 허용된 무료 API를 아직 확정하지 못함

## 공급원별 결론

| 공급원 | 무료 범위 | Top 200 일봉 가능성 | 공개 재배포 판단 | 결론 |
|---|---|---|---|---|
| Yahoo Finance/yfinance | 비공식 라이브러리를 통한 공개 웹 데이터 | 개발 실측 191/200 | Yahoo 약관상 개인 사용 및 대체 DB/피드 제한이 있어 파생 신호 공개까지 명확하지 않음 | 개발 검증만, 예약 운영 금지 |
| Alpha Vantage | 무료 25요청/일, 일부 오픈소스·교육 프로젝트 추가 한도 신청 가능 | 종목별 호출이면 매일 200개에 부족 | 공식 정책은 개인 비상업용과 사업용 문의를 구분 | 자동 운영 후보 아님 |
| Alpaca Basic | 무료, 미국 주식, 2016년 이후, 인증 필요, 실시간은 IEX 중심 | 이력 범위는 가능 | 고객 약관은 서면 동의 없는 시장 데이터 복제·배포·상업 이용을 제한 | 내부 연구만 가능성, 공개 게시 불가 판정 |
| Nasdaq Data Link | 데이터셋별 무료/유료 | 데이터셋별 상이 | 기본 약관은 파생데이터 외부 배포도 주문서 또는 사전 서면승인을 요구 | 무료 공개 운영 부적합 |
| IEX 직접 지연 데이터 | 공식 요금표상 15분 지연 데이터의 재배포 조항 존재 | 직접 피드·계약·수집 인프라 확인 필요 | 지연 데이터 재배포 가능성은 있으나 API 접근·계약 조건을 아직 검증하지 못함 | 유일한 추가 조사 후보 |
| Stooq 등 기타 무료 사이트 | 공개 다운로드가 알려져 있음 | 기술적으로 가능할 수 있음 | 공식 영문 재배포 허가를 확인하지 못함 | 자동 경로에 연결하지 않음 |

## 공식 근거

- Yahoo Terms of Service: https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html
- Yahoo Finance data disclaimer: https://help.yahoo.com/kb/SLN2352.html
- Alpha Vantage support/rate limits: https://www.alphavantage.co/support/
- Alpha Vantage market data policy: https://www.alphavantage.co/realtime_data_policy/
- Alpaca market data plans: https://docs.alpaca.markets/us/docs/about-market-data-api
- Alpaca terms: https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf
- Nasdaq Data Link license terms: https://data.nasdaq.com/terms
- IEX fee schedule and delayed-data terms: https://www.iex.io/resources/trading/fee-schedule

## 운영 결정

1. 현재 Yahoo/yfinance 경로는 개발자가 명시적으로 실행하는 연구 검증에만 사용한다.
2. 원시 가격 캐시는 Git에 커밋하지 않으며 Streamlit 앱은 외부 시세 API를 호출하지 않는다.
3. 예약 GitHub Actions 수집과 운영 신호 게시 기능은 추가하지 않는다.
4. IEX 직접 지연 데이터는 무료 API, 계약, 200개 일봉 이력, 외부 파생 신호 표시 권리를
   서면으로 확인한 뒤에만 어댑터 후보로 검토한다.
5. 유료 라이선스나 공급원 계약이 필요해지면 가격과 권한 범위를 먼저 보고하고 승인 전에는
   구매·가입·API 키 생성을 하지 않는다.
