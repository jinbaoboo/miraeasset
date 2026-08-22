# 샘플 원문 대조 및 검증 기록

## 샘플 구성

전체 배치 전후에 20건을 선택했다. 전체 실행에서 새로 발견한 malformed XML과 PDF fallback 샘플도 회귀군에 추가했다.

- 정기공시 9건: annual, half, quarter, correction annual/half, PDF correction quarter, 세 종류의 malformed attribute quote
- 주요사항 3건: 유상증자, 합병, 자기주식 취득 정정
- 거래소 5건: 공급계약 정정, 계약 해지, 시설투자, 자유형 주요경영사항, 콜옵션 계약
- 지분 3건: 일반, 약식, 약식 정정

삼성전자·삼성전기·LG이노텍·레인보우로보틱스·에코프로비엠·LG에너지솔루션·SK하이닉스·고려아연 등 서로 다른 산업과 양식을 포함했다. 삼성전자 annual 샘플에는 감사보고서와 연결감사보고서 첨부 XML이 있다.

## 직접 원문 대조 예

| 문서 | 원문 확인 항목 | 구조화 결과 | 판정 |
|---|---|---|---|
| `periodic_20230515002335` | 연결 손익계산서, 영업이익, 제55기 1분기 3개월/누적 `640,178`, 단위 백만원 | row label·다단 header·numeric value `640178`·scale `1000000` 복원 | 정상 |
| `periodic_20230515002335` | raw에 bare `&` 245개와 비태그 angle text 10개 | strict 실패 후 메모리 복구, raw hash와 repair count 기록 | 정상(경고) |
| `periodic_20240312000736` | main XML 외 `_00760`, `_00761` 감사 첨부 | main/audit/consolidated audit file role 분리 | 정상 |
| `periodic_20250320001103` | `ENG="...(\"FVOCI')..."` 속성 내 불일치 따옴표 | 인용된 acronym의 따옴표를 정규화해 메모리 복구 | 정상(경고) |
| `periodic_20260313001191` | `ENG="" KB Insurance...` 공백 앞 중복 따옴표 | 정상 빈 속성과 구분해 메모리 복구 | 정상(경고) |
| `periodic_20250318001196` | `ENG="...("FVTPL")"` 속성 내 비 escape 따옴표 | 속성 내 인용부호만 entity로 복구 | 정상(경고) |
| `periodic_20240514001522` | XML 없이 252페이지 PDF와 viewer HTML만 존재 | 277개 페이지 locator 본문 chunk, 표/정정 구조 한계 warning | 정상(경고) |
| `major_20241118000171` | 위탁투자중개업자 정정 전 `삼성증권 등`, 정정 후 복수 증권사 | correction item before/after와 current effective `after` 보존 | 정상 |
| `exchange_20250731800028` | 실제 HTML, 계약상대 테슬라, 계약금액 `22,764,764,160,000`, 매출 대비 `7.6` | HTML grid, `contract_amount_krw`, `counterparty`, `revenue_ratio_pct` 복원 | 정상 |
| `holding_20241025000530` | 직전/이번 보유주식 수·비율, 다단 표와 다수 특별관계자 | logical table/cell relation과 correction block 보존 | 정상(검색 PII 마스킹) |
| `exchange_20230315902426` | 콜옵션 주식수 `8,550,439주`가 자유서술 문장 안에 존재 | 독립 숫자 cell로 오인하지 않고 전체 문장을 body/search text에 보존 | 정상 |

## 자동 교차 검증

`validation/validate_cross_group_samples.py`가 문서 메타데이터, 파싱 상태, section hierarchy, 본문, logical table, normalized cell, scope enum, correction, event, 첨부 및 숫자 문맥을 검사했다.

- 샘플: 20건
- 검사 및 통과: 173/173
- 실패: 0개

상세 표는 `validation/results/cross_group_validation.md`, 기계 판독 결과는 `cross_group_validation.json`에 있다.

`warning`은 대개 메모리 XML 복구 또는 실제 asset이 없는 이미지 파일명 때문에 발생하며 문서 실패를 뜻하지 않는다. 파싱 오류와 기대 가능한 복구 경고를 분리해 집계했다.

## 발견 후 반영한 수정

1. 거래소 확장자만 보고 XML 파서로 보내던 위험을 제거하고 HTML 파서를 분리했다.
2. 자유서술에 포함된 숫자를 독립 정형 cell로 강제하지 않고 본문을 보존했다.
3. 9만 개 이상 셀이 있는 대형 지분공시에서 period 판정을 cell마다 반복하던 병목을 column cache로 줄였다.
4. 계산용 `cells`는 전부 보존하되 중복 검색 보조 데이터인 `event_fields`만 5,000개로 제한했다.
5. 지분공시 검색 텍스트에서 개인 식별 패턴을 마스킹했다.
