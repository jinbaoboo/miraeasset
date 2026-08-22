# 정기공시 XML 구조화 미니 프로젝트 분석 보고서

- 분석일: 2026-08-13
- 원본 위치: `/Users/kimjinho/Downloads/3.공시/corpus` (파일시스템의 실제 폴더명은 한글 NFD 형식)
- 원칙: 원본 raw 데이터 읽기만 수행. 전체 일괄 처리, Vector DB 구축, 임베딩 생성은 수행하지 않음.
- 범위: 정기공시 본문 XML 4건과 사업보고서 첨부 XML 2건을 직접 비교함.

## 1. 확인한 샘플 문서 목록

| 역할 | 회사 | 보고서 | 접수번호 | 기준기간 | 파일 수 | 확인 파일 |
|---|---|---|---|---|---:|---|
| annual | 삼성전자 | 사업보고서 | `20240312000736` | 2023.12 | 3 | 본문 `20240312000736.xml`, 감사보고서 `_00760.xml`, 연결감사보고서 `_00761.xml` |
| half | 삼성전자 | 반기보고서 | `20230814002534` | 2023.06 | 1 | `20230814002534.xml` |
| quarter | 삼성전자 | 분기보고서 | `20230515002335` | 2023.03 | 1 | `20230515002335.xml` |
| correction | 삼성전기 | `[기재정정]사업보고서` | `20240329002895` | 2023.12 | 1 | `20240329002895.xml` |

정정 전 삼성전기 원문은 `20240312000778`이며, 정정 XML의 `CORRECTION`에는 최초제출일 `2024-03-12`, 정정일 `2024-03-29`, 정정 전/후 내용이 들어 있다.

참고로 manifest의 정기공시는 1,054건이다. 이 가운데 XML은 1,051건이며, `pdf+html` 대체본은 3건이다. 정정 XML은 annual 83건, half 28건, quarter 46건이고, 별도로 `pdf+html` 정정 2건이 있다.

## 2. 정기공시 XML 구조 분석

### 2.1 문서 공통 골격

확인한 본문 문서는 대체로 다음 골격을 가진다.

```text
DOCUMENT
├─ DOCUMENT-NAME
├─ FORMULA-VERSION
├─ COMPANY-NAME
├─ SUMMARY / EXTRACTION*
└─ BODY
   ├─ COVER
   ├─ CORRECTION?                 # 기재정정 문서에 존재
   ├─ SECTION-1                   # 대분류
   │  ├─ TITLE
   │  └─ LIBRARY? / SECTION-2*    # LIBRARY는 투명 래퍼로 보아야 함
   │     ├─ TITLE
   │     └─ SECTION-3*            # 일부 항목만 사용
   ├─ P / SPAN / PGBRK
   ├─ TABLE-GROUP / TABLE
   └─ IMAGE
```

`SECTION-1`의 대분류 골격은 사업·반기·분기보고서가 거의 같다. 예를 들면 `I. 회사의 개요`, `II. 사업의 내용`, `III. 재무에 관한 사항`, `IV. 이사의 경영진단 및 분석의견`, `XII. 상세표` 등이다. `SECTION-2`는 `LIBRARY` 아래에 들어가기도 하므로 `SECTION-1 > SECTION-2` 직접 자식만 찾으면 사업 내용과 재무제표 일부가 누락된다.

대분류/중분류는 `SECTION-1/2/3`와 그 첫 `TITLE`로 비교적 안정적으로 표현된다. 그보다 작은 제목은 독립 `TITLE`일 수도 있고, `P` 안의 굵은 `SPAN USERMARK="... B"`일 수도 있다. 따라서 제목 탐지는 태그명만으로 끝낼 수 없다.

### 2.2 문서 제목과 식별자

- `DOCUMENT-NAME`: `사업보고서`, `반기보고서`, `분기보고서`; `ACODE`는 각각 `11011`, `11012`, `11013`.
- `COVER-TITLE`: 화면용 표지 제목(예: `사 업 보 고 서`).
- `COMPANY-NAME`: XML 안 표기에는 `주식회사`, `(주)` 포함 여부가 문서마다 달랐다. 기업 조인에는 manifest의 `corp_code`, `corp_name`, `listed_name`을 사용해야 한다.
- `FORMULA-VERSION`: 샘플에서 5.0, 5.2, 5.5가 혼재했다. 버전에 따른 구조 차이를 대비해야 한다.
- 정정 문서의 내부 `DOCUMENT-NAME`은 여전히 `사업보고서`다. `[기재정정]` 여부와 기준기간은 manifest의 `report_nm`, `is_correction`, `base_year`, `base_month`가 권위 있는 값이다.

### 2.3 일반 본문과 주석

- `P`: 기본 문단 단위.
- `SPAN`: 문단 안의 인라인 조각, 강조 또는 서식 범위. `P.text`만 읽지 말고 자식과 tail text까지 문서 순서로 합쳐야 한다.
- `USERMARK`: `F-14`, `B` 같은 서식 힌트. `B`는 작은 제목 후보 탐지에 보조적으로 유용하지만 의미 필드로 믿으면 안 된다.
- `PGBRK`: 페이지 경계. 의미 분할의 강한 기준은 아니지만 source locator와 긴 섹션 분할의 보조 신호로 보존할 가치가 있다.
- `※`, `주)`, `주석`, `[△는 부(-)의 값임]` 같은 내용은 별도의 고정 `FOOTNOTE` 태그가 아니라 `P`, `TD`, `TE`에 들어간다. 표 직후의 주석은 표와 연결해야 한다.
- `A REFNO`: 본문과 `XII. 상세표` 사이 내부 참조. 링크 텍스트와 `REFNO`를 보존하면 관련 chunk 연결에 쓸 수 있다.

### 2.4 표 구조

기본 표는 다음 요소로 구성된다.

```text
TABLE-GROUP?               # 의미상 여러 TABLE을 묶는 경우
├─ TITLE?                  # 표 또는 재무제표 제목
└─ TABLE+
   ├─ COLGROUP / COL       # 표시 폭
   ├─ THEAD? / TBODY
   └─ TR
      └─ TH | TD | TE | TU
```

- `TH`: 헤더 셀.
- `TD`: 일반 셀. 반기·분기 재무제표의 수치도 `TD`에 있으므로 `TE`만 수치 셀로 간주하면 안 된다.
- `TE`: DART 추출용 셀. `ACODE`가 있을 수 있으나 모든 `TE`에 코드가 있지는 않고, 일반 문장도 포함한다.
- `TU`: 단위/기준일 등 추출용 셀. `AUNIT`, `AUNITVALUE`가 있다. 예: `PERIODFROM=20230101`, `PERIODTO=20231231`, `WONSTOCK=1`. 그러나 금액 단위가 항상 `TU`인 것은 아니다.
- `ROWSPAN`, `COLSPAN`: 다단 헤더와 행 계층을 표현한다. 반드시 2차원 grid로 확장하되 원래 span도 함께 보존해야 한다.
- `TABLE-GROUP ACLASS`: 표의 의미를 강하게 암시한다. 예: `{XBRL}BS_C` 연결 재무상태표, `{XBRL}BS_S` 별도 재무상태표, `{XBRL}IS_C2` 연결 손익계산서, `{XBRL}CF_S` 별도 현금흐름표, `{XBRL}NT_C_*` 연결 주석, `{XBRL}NT_S_*` 별도 주석.

표 하나가 반드시 의미 단위 하나인 것은 아니다. 삼성전자 연결 재무상태표는 하나의 `TABLE-GROUP` 안에서 첫 `TABLE`이 제목·기간·단위를, 둘째 `TABLE`이 실제 수치를 담는다. 반대로 연구개발비 표는 제목/단위용 무테두리 표, 본표, 주석용 무테두리 표가 별도 `TABLE` 세 개로 연속하며 `TABLE-GROUP`이 없다. 따라서 `TABLE` 단독 처리보다 “표 그룹 + 인접 문맥 결합”이 필요하다.

### 2.5 표 제목, 단위, 연결/별도, 기간

| 정보 | 실제 표현 | 처리 원칙 |
|---|---|---|
| 표 제목 | `TABLE-GROUP/TITLE`, 직전 `TITLE`·`P`·굵은 `SPAN`, 첫 표의 첫 행 | 후보를 모아 우선순위로 결정하고 원문 후보도 보존 |
| 금액 단위 | `(단위 : 백만원)` 등이 제목용 표의 `TE/TD/P`, 본표 직전 무테두리 표, 드물게 `TU` | 현재 표뿐 아니라 같은 그룹 및 앞뒤 인접 표/문단에서 탐색 |
| 연결/별도 | 제목의 `연결`, `TABLE-GROUP@ACLASS`의 `_C`/`_S`, 주석 제목의 `(연결)` | 제목을 최우선으로 하고 ACLASS는 보조. interim의 연결 표는 `{XBRL}BS`, `{XBRL}IS2`처럼 `_C`가 없을 수 있음 |
| 당기/전기 | `제55기`, `제54기`, `당기`, `전기`, 실제 날짜 | 회차명만 저장하지 말고 날짜/기간으로 정규화 |
| 시점/기간 | `2023.12.31 현재` 또는 `2023.01.01부터 2023.12.31까지` | `instant`와 `duration` 구분 |
| 분기/반기 열 | 다단 `TH`: `제55기 반기 > 3개월/누적` | 각 leaf column에 전체 header path를 저장 |
| 부호 | `△13,045`, 괄호, `-`, 빈칸 | raw 값 유지, 파싱된 숫자는 별도 필드. `-`와 빈칸을 자동으로 0으로 바꾸지 않음 |

annual 손익계산서는 3개 연도, half/quarter 손익계산서는 당기·전기의 `3개월`과 `누적` 열을 함께 갖는다. 분기 1분기는 3개월과 누적 값이 같아도 서로 다른 열 의미를 유지해야 한다.

### 2.6 연결/별도 재무제표와 주석

사업보고서에는 연결 재무제표/연결 주석과 별도 재무제표/별도 주석이 모두 들어 있다. 제목과 ACLASS를 제거한 채 수치만 색인하면 같은 계정·같은 연도의 연결/별도 값이 충돌한다.

재무제표의 `TABLE-GROUP@ACLASS`는 유용하지만 문서 유형별로 명명 규칙이 완전히 같지 않다. annual은 `{XBRL}BS_C`, interim은 연결 재무상태표가 `{XBRL}BS`였다. 따라서 `basis`는 다음 순서로 판정하는 것이 안전하다.

1. 제목/헤더에 `연결`이 있으면 `consolidated`.
2. ACLASS에 `_C` 또는 `NT_C_`가 있으면 `consolidated`.
3. ACLASS에 `_S` 또는 `NT_S_`가 있으면 `separate`.
4. 연결 재무제표 묶음 뒤의 비연결 제목은 `separate` 후보로 보되, 명시 근거가 없으면 `unknown`.

### 2.7 첨부 XML

삼성전자 annual 폴더의 3개 파일은 다음과 같다.

- `20240312000736.xml`: 본문, `DOCUMENT-NAME ACODE="11011"`.
- `20240312000736_00760.xml`: 감사보고서, `ACODE="00760"`.
- `20240312000736_00761.xml`: 연결감사보고서, `ACODE="00761"`.

첨부는 본문과 동일한 `DOCUMENT` 계열 구조이며 자체 `SECTION`, `TABLE`, `IMAGE`를 갖는다. main XML 안에 첨부 파일 경로가 명시적으로 연결되어 있지 않으므로 같은 폴더/접수번호 prefix와 첨부 XML의 `DOCUMENT-NAME`, `ACODE`로 관계를 구성해야 한다. 첨부 chunk는 원 보고서 `doc_id` 아래 두되 `source_file_role`을 분리해야 중복과 출처 혼동을 줄일 수 있다.

### 2.8 정정공시

정정 샘플에는 `BODY/CORRECTION`이 있고 다음이 포함된다.

- 정정 제목과 정정일
- 정정대상 서류와 최초제출일
- 정정 항목, 정정 사유, 정정 전, 정정 후의 표
- 이후 정정된 전체 본문

정정 샘플은 대표이사 확인 섹션이 두 번 나타나는 등 정정 머리말과 전체 본문 결합 때문에 중복 구조가 생긴다. 검색 시에는 다음 두 관점을 모두 만들어야 한다.

1. `correction_diff`: 무엇이 왜 어떻게 바뀌었는지 검색하는 정정 전/후 chunk.
2. `current_content`: 정정 후 전체 본문에서 검색하는 최신 버전 chunk.

원본과 정정본을 동등하게 검색하면 오래된 수치와 최신 수치가 함께 반환된다. 원본을 삭제하지 말고 version chain을 만들며, 기본 검색은 최신 유효본을 우선해야 한다.

## 3. 주요 XML 태그와 의미

| XML 태그/속성 | 실제 의미 | 구조화 시 주의 |
|---|---|---|
| `DOCUMENT` | 파일 루트 | 원 공시 본문과 첨부 모두 같은 루트 사용 |
| `DOCUMENT-NAME@ACODE` | 문서 종류와 DART 양식 코드 | 정정 표시는 포함하지 않음 |
| `COMPANY-NAME@AREGCIK` | XML 내 회사명과 corp code | 회사명 문자열은 manifest와 다를 수 있음 |
| `FORMULA-VERSION@ADATE` | 공시 양식 버전 | 파서 회귀 테스트의 버전 축으로 보존 |
| `SUMMARY/EXTRACTION` | 일부 문서 수준 추출값 | 문서마다 항목이 크게 달라 필수값으로 가정 금지 |
| `COVER`, `COVER-TITLE` | 표지 | 제출기간/회사 정보 추출 보조, 검색 우선순위는 낮음 |
| `CORRECTION` | 정정 신고 블록 | manifest `is_correction`과 함께 사용 |
| `SECTION-1/2/3` | 대/중/하위 구조 컨테이너 | `LIBRARY`를 투명하게 통과해 계층 구성 |
| `TITLE` | 섹션 또는 표 제목 | 어느 레벨에도 나타날 수 있고 interim 표에는 없을 수 있음 |
| `LIBRARY` | DART 양식 조각 래퍼 | 의미 계층으로 추가하지 않음 |
| `P` | 문단 | mixed content와 공백 정규화 주의 |
| `SPAN` | 인라인 서식/강조 | 자식 텍스트와 tail text를 순서대로 병합 |
| `PGBRK` | 페이지 나눔 | 보조 locator, 강제 chunk 경계로만 쓰지 않음 |
| `TABLE-GROUP@ACLASS` | 관련 표 묶음 및 DART/XBRL 의미 코드 | 제목·기간·단위·본표를 함께 묶는 주요 단위 |
| `TABLE@ACLASS` | 개별 표 | `NORMAL`, `EXTRACTION`만으로 중요도 판단 금지 |
| `THEAD`, `TBODY`, `TR` | 표의 헤더/본문/행 | `THEAD`가 없는 표도 있음 |
| `TH` | 헤더 셀 | 다단 헤더 span 확장 필요 |
| `TD` | 일반 셀 | 수치/본문/주석 모두 가능 |
| `TE@ACODE` | DART 추출 셀 | 코드가 없는 경우도 많고, 일반 서술도 포함 |
| `TU@AUNIT,@AUNITVALUE` | DART 단위·기간 등 추출 셀 | 표시 텍스트와 정규화 값을 함께 보존 |
| `COLGROUP/COL` | 표시용 열 폭 | 의미 열 판정의 보조 정보에만 사용 |
| `IMAGE/IMG` | 이미지 파일 참조 | `IMG` 텍스트가 파일명이며 바이너리는 XML에 없음 |
| `IMG-CAPTION` | 이미지 캡션 | 이미지 분류와 검색용 텍스트로 활용 |
| `A@REFNO` | 문서 내부 참조 | 상세표와 본문 chunk를 연결 가능 |

## 4. 정기공시에서 검색·추출 가치가 높은 정보

| 정보 | 주 위치 | 권장 우선순위 | 검색/비교 시 핵심 조건 |
|---|---|---:|---|
| 매출액 | 재무제표·요약재무·사업부문 표, 보조 본문 | 최상 | 연결/별도, 누적/3개월, 기간, 단위 |
| 영업이익 | 손익계산서·부문 표 | 최상 | 연결/별도, 부문, 기간, 단위 |
| 당기순이익 | 손익계산서·요약재무 표 | 최상 | 지배/비지배 귀속 구분 가능성 |
| 자산·부채·자본 | 재무상태표·요약재무 표 | 최상 | 시점 값, 연결/별도, 유동/비유동 |
| 사업부문별 매출·이익 | 사업 내용 및 재무제표 주석 표 | 최상 | 내부거래 제거 여부, 부문 정의 변화 |
| 설비투자·CAPEX | 생산설비/투자 현황 표와 본문, 현금흐름·유형자산 주석 | 상 | 계획/실적 구분, 투자기간, 단위 |
| 연구개발비 | 본문 요약 + 연구개발비 표 | 상 | 총액/비용처리/자산화/정부보조금, 연결 누계 기준 |
| 주요 사업·제품 | 사업의 개요, 주요 제품 및 서비스 본문·표 | 상 | 부문·제품 계층과 매출 연결 |
| 생산능력·생산실적·가동률 | 사업 내용 표와 산정방법 본문 | 상 | 물량 단위, 연결 기준, 산식 |
| 수주·주요 계약 | 매출 및 수주상황, 주요계약 본문·표 | 상 | 계약/수주잔고, 상대방, 기간. 이벤트 최신성은 주요사항·거래소 공시와 교차 검색 필요 |
| 투자 계획 | 생산설비 및 투자 현황 본문·표 | 상 | 계획과 집행액, 대상 시설, 기간 |
| 시장·산업 변화 | 기타 참고사항/사업부문별 현황 본문 | 상 | 전망인지 실적 설명인지 구분 |
| 위험·우발부채 | 위험관리 본문·표, 재무제표 주석 | 중상 | 환율 민감도, 소송·보증·약정의 기준일 |
| 배당·자금조달 | III. 재무에 관한 사항 표 | 중 | 회차/주당액/발행·사용 목적 |

수치 질문은 표가 1차 근거이고 본문은 정의·산정방법·예외 설명의 2차 근거다. 연구개발비처럼 본문과 표가 모두 중요한 경우에는 두 chunk를 `related_chunk_ids`로 연결해야 한다.

## 5. 본문 처리 방법

1. 원본 bytes와 manifest 메타데이터를 읽되 raw는 수정하지 않는다.
2. 엄격 파싱 전 lexical validation을 실행한다.
3. 알려진 DART 태그만 태그로 인정하고, 본문 속 bare `&`와 `< ... >`는 메모리상에서만 escape하는 보존적 정제를 적용한다.
4. 복구 파서를 fallback으로 쓰되, 복구 오류 수와 정제 로그를 문서 품질 메타데이터로 남긴다.
5. `LIBRARY`를 투명 래퍼로 처리해 `SECTION-1/2/3`와 `TITLE`의 `section_path`를 만든다.
6. `P`의 mixed content를 문서 순서대로 평탄화하되, 문장 사이 공백을 넣고 과도한 공백만 정규화한다.
7. 굵은 `SPAN`과 번호 패턴(`가.`, `(1)`)은 하위 제목 후보로 사용하되 원문의 정식 섹션과 구분해 `heading_source=style_inferred`로 기록한다.
8. 빈 문단, 순수 레이아웃 표, 반복 표지는 검색 색인에서 낮은 우선순위를 주되 source map에는 남긴다.
9. 본문 chunk의 맨 앞에 회사·보고서·기준기간·section path를 사람이 읽을 수 있는 짧은 context header로 합성한다.

## 6. 표 처리 방법

1. `TABLE-GROUP`이 있으면 이를 1차 의미 단위로 사용한다.
2. 그룹이 없으면 직전 제목/문단, 연속된 무테두리 제목·단위 표, 본표, 직후 `※` 주석 표를 하나의 logical table로 결합한다.
3. `TH`, `TD`, `TE`, `TU`를 모두 셀로 수집하고 원 태그/속성을 보존한다.
4. `ROWSPAN`, `COLSPAN`을 적용한 rectangular grid와 원본 sparse cell 목록을 둘 다 저장한다.
5. 다단 헤더는 `['제55기 반기', '누적']`처럼 leaf column의 header path로 만든다.
6. 제목, 단위, 연결/별도, 기간을 표 문맥에서 추출하고 각 추론의 `evidence`를 저장한다.
7. 숫자는 `raw_value`와 `numeric_value`를 함께 둔다. 쉼표, `%`, `△`, 괄호 음수, 통화, 주식 수를 분리 파싱한다.
8. 행 이름과 모든 열 의미를 결합한 search text를 생성한다. 예: `삼성전자 | 연결 손익계산서 | 제55기 반기 누적 | 영업이익 | 1,308,725 백만원`.
9. 큰 표 분할 시 각 chunk에 table title, unit, basis, 전체 column header를 반복하고 행 그룹만 나눈다.
10. 표 직후 주석은 별도 note로도 저장하되 `table_id`로 연결한다.

## 7. 이미지 처리 방법

샘플의 `IMAGE`는 base64나 binary를 담지 않고 `IMG` 안에 파일명만 둔다. 그러나 해당 JPG는 raw 폴더에 존재하지 않았다.

확인한 이미지는 다음 두 범주였다.

- 서명/대표이사 확인서/내부회계 운영실태 서명본: 법적 확인 이미지이나 RAG용 OCR 가치는 낮음.
- 연구개발 조직도: 사업·연구 조직을 담은 의미 있는 이미지로 분류 가능.

초기 MVP에서 할 일:

- 모든 `IMAGE`에 대해 파일명, caption, section path, 크기, asset 존재 여부를 저장한다.
- 캡션·주변 문맥으로 `decorative_or_signature`, `semantic_diagram`, `numeric_table_image`, `unknown`을 분류한다.
- 숫자/표가 이미지로만 제공되고 실제 asset을 확보할 수 있다면 OCR 및 표 복원이 필수다. OCR 결과에는 낮은 신뢰도와 원 이미지 locator를 표시한다.
- 의미 있는 조직도·공정도는 asset을 확보할 수 있을 때 OCR/vision 요약 대상으로 보낸다.

나중에 처리해도 되는 것:

- 대표이사 서명, 확인서 스캔의 본문 OCR.
- 장식 로고, 반복 표지 이미지.

현재 corpus만으로는 이미지 OCR이 불가능하므로, DART viewer 또는 별도 원문 패키지에서 referenced asset을 추가 확보할지 먼저 결정해야 한다.

## 8. 권장 JSON 스키마

권장 저장 단위는 `document`와 `chunk`를 분리하는 것이다. manifest 메타데이터는 document 레코드에 정규화하고, 검색 대상 chunk에는 요청된 필드를 비정규화해 반복한다. 아래는 chunk 중심의 권장 형태다.

```json
{
  "schema_version": "1.0",
  "chunk_id": "periodic_20240312000736:main:table:000123:rows_000_024",
  "doc_id": "periodic_20240312000736",
  "corp_code": "00126380",
  "corp_name": "삼성전자",
  "listed_name": "삼성전자",
  "report_nm": "사업보고서 (2023.12)",
  "rcept_no": "20240312000736",
  "rcept_dt": "20240312",
  "doc_group": "periodic",
  "doc_subtype": "annual",
  "base_year": 2023,
  "base_month": 12,
  "is_correction": false,

  "source_file": "20240312000736.xml",
  "source_file_role": "main",
  "document_name": "사업보고서",
  "document_acode": "11011",
  "formula_version": "5.5",
  "source_locator": {
    "xml_path": "/DOCUMENT/BODY/SECTION-1[4]/LIBRARY/SECTION-2[2]/TABLE-GROUP[1]",
    "element_ordinal": 123,
    "page_break_before": 41
  },

  "section": "III. 재무에 관한 사항",
  "subsection": "2. 연결재무제표",
  "section_path": [
    "III. 재무에 관한 사항",
    "2. 연결재무제표",
    "2-2. 연결 손익계산서"
  ],
  "content_type": "table",
  "text": "삼성전자 | 사업보고서 (2023.12) | 연결 손익계산서 ...",

  "table_id": "periodic_20240312000736:main:table:000123",
  "table_title": "연결 손익계산서",
  "unit": {
    "raw": "(단위 : 백만원)",
    "currency": "KRW",
    "scale": 1000000,
    "quantity": "money"
  },
  "basis": "consolidated",
  "statement_type": "income_statement",
  "period_type": "duration",
  "periods": [
    {
      "column_key": "fy55",
      "label": "제55기",
      "start_date": "2023-01-01",
      "end_date": "2023-12-31",
      "comparison_role": "current"
    }
  ],
  "columns": [
    {
      "column_key": "fy55",
      "header_path": ["제55기"],
      "period_ref": "fy55"
    }
  ],
  "rows": [
    {
      "row_key": "operating_profit",
      "row_header": ["영업이익"],
      "cells": [
        {
          "column_key": "fy55",
          "raw_value": "6,566,976",
          "numeric_value": 6566976,
          "value_type": "number",
          "is_missing": false
        }
      ]
    }
  ],
  "header_rows_raw": [],
  "cells_raw": [],
  "notes": ["[△는 부(-)의 값임]"],
  "related_chunk_ids": [],

  "correction": {
    "version_role": "original",
    "supersedes_doc_id": null,
    "superseded_by_doc_id": null,
    "is_latest_version": true
  },
  "parse_quality": {
    "strict_xml_valid": false,
    "repair_applied": true,
    "repair_issue_count": 543,
    "warnings": ["unescaped_ampersand", "bare_angle_bracket_text"]
  }
}
```

스키마 설계 원칙:

- `text`는 검색/RAG용 렌더링이고, `rows/cells`는 수치 비교용이다. 하나를 다른 하나로 대체하지 않는다.
- `section`/`subsection`은 편의 필드, `section_path`가 원 계층의 기준이다.
- 표의 period와 unit은 셀 의미를 결정하므로 chunk마다 반복한다.
- `source_locator`와 raw cell 정보를 남겨 답변 근거를 원 XML까지 추적할 수 있게 한다.
- `source_file_role`은 `main`, `audit_report`, `consolidated_audit_report`, `other_attachment`를 권장한다.
- `content_type`은 최소 `text`, `table`, `table_note`, `image`, `correction_diff`, `attachment_meta`로 구분한다.
- 원문 전체 XML을 chunk에 복제하지는 않되, raw 파일의 hash를 document 레코드에 저장해 재현성을 확보한다.

## 9. 권장 Chunk 전략

### 9.1 본문

1. `section_path`가 바뀌면 새 chunk 후보를 시작한다.
2. 같은 소제목 아래의 연속 문단은 의미가 이어지는 범위에서 묶는다.
3. 제목만 있는 chunk는 만들지 않고 뒤의 본문에 붙인다.
4. 매우 긴 재무 주석은 제목/번호 문단과 표 경계를 활용해 분할한다.
5. 목표 크기는 고정 규칙이 아니라 soft limit로 둔다. 초기 실험값은 본문 1,000~2,000자 또는 500~900 한국어 토큰 정도가 적절하나, 문장과 목록을 깨지 않는다.
6. overlap은 같은 section 안에서만 1~2문장 사용하고 다른 section을 섞지 않는다.

### 9.2 표

- 작은/중간 표: logical table 전체를 한 chunk로 유지.
- 큰 표: 의미 있는 행 그룹 또는 20~40행 단위로 나눈다.
- 각 분할 chunk에 제목, 단위, 연결/별도, 기간, 전체 다단 column header, 표 주석을 반복한다.
- 행의 계층(자산 > 유동자산 > 현금및현금성자산)을 각 row에 누적해 숫자가 계정명과 분리되지 않게 한다.
- 텍스트 검색용 직렬화와 구조화 rows를 함께 생성한다.
- 연결/별도 표, 당기/전기 열, 3개월/누적 열을 절대 합쳐 쓰지 않는다.

### 9.3 고정 길이 vs 섹션 기반

| 방식 | 장점 | 단점 | 판단 |
|---|---|---|---|
| 일정 글자 수 | 구현이 단순하고 chunk 크기가 균일 | 제목·표·단위·기간 관계가 끊기고 숫자만 남을 수 있음 | fallback/soft limit로만 사용 |
| 섹션 기반 | 공시의 공식 목차를 보존하고 근거 설명이 쉬움 | 섹션 크기 편차가 크고 작은 제목이 일관된 태그가 아님 | 기본 전략 |
| 섹션 + 의미 경계 + soft limit | 구조 보존과 검색 크기를 절충 | 구현·검증 비용 증가 | 권장 |

### 9.4 검색 시 권장 계층

Vector DB는 아직 만들지 않지만, 다음 단계에서 hybrid retrieval을 염두에 둔 출력이 좋다.

1. metadata filter: 회사, 보고서 유형, 기준기간, 최신 유효본, 연결/별도.
2. lexical/semantic search: section path와 본문/표 직렬화.
3. numeric lookup: 정규화된 row/cell에서 항목·기간·단위 기준 비교.
4. evidence rendering: 원문 값, 표 제목, 단위, section path, 접수번호를 함께 반환.

## 10. 예외적으로 처리해야 할 XML 구조

1. **엄격 XML이 아님**: `R&D`, `S&P`의 bare `&`, `< TV 시장점유율 추이 >` 같은 본문이 escape되지 않았다. 삼성전자 annual은 복구 파서 기준 543개 오류였다.
2. **복구 파서의 의미 손실 위험**: `<보수지급금액 5억원 이상...>` 같은 텍스트를 임의 태그로 해석할 수 있다. 허용 태그 allowlist 기반 사전 정제가 필요하다.
3. **`LIBRARY` 래퍼**: 실제 section 계층 사이에 끼어 있으므로 투명 처리해야 한다.
4. **제목 표현 불일치**: `TITLE`, 굵은 `SPAN`, 표 첫 행, 직전 무테두리 표 등 여러 방식이 있다.
5. **interim XBRL ACLASS 차이**: annual 연결 표는 `_C`, half/quarter 연결 표는 `_C` 없는 `{XBRL}BS`, `{XBRL}IS2`일 수 있다.
6. **복합 표**: 제목/기간/단위와 본표가 서로 다른 `TABLE`에 있다.
7. **다단/병합 셀**: `ROWSPAN/COLSPAN`을 무시하면 열과 숫자의 대응이 틀어진다.
8. **셀 태그 혼용**: 수치는 `TD`, `TE`, `TU` 어디에도 있을 수 있다.
9. **표 주석의 독립 표화**: 주석이 직후 무테두리 `TABLE`에 들어갈 수 있다.
10. **정정 문서 중복**: `CORRECTION` 뒤에 정정 후 전체 문서가 있고 일부 section이 중복될 수 있다.
11. **다중 정정**: 같은 회사·기준기간에 정정본이 여러 번 존재할 수 있어 단순 boolean보다 version chain이 필요하다.
12. **첨부 XML**: 같은 접수번호 폴더에 감사/연결감사보고서가 있고 main과 별도 문서명을 갖는다.
13. **이미지 asset 누락**: XML에는 JPG 파일명만 있고 raw 폴더에 실제 파일이 없었다.
14. **빈칸과 표시값**: 빈칸, `-`, `해당사항 없음`, 0은 서로 다르다.
15. **회사명 표기 흔들림**: `삼성전자`, `삼성전자주식회사`, `삼성전자(주)`가 혼재하므로 manifest 키를 사용한다.
16. **비XML 대체본**: 정기공시 3건은 `pdf+html`이므로 별도 파이프라인이 필요하다.

## 11. 실제 전처리 코드를 작성하기 전에 결정해야 할 사항

1. **정정 버전 정책**: 기본 검색은 최신 정정본만 노출할지, 원본도 낮은 점수로 노출할지 결정. 권장은 원본 보존 + 최신 유효본 기본 필터 + 정정 diff 별도 색인이다.
2. **정정 chain 매핑 규칙**: `corp_code + doc_subtype + base_year + base_month`와 `CORRECTION`의 최초제출일을 조합하고, 다중 정정은 접수순으로 연결할지 확정한다.
3. **XML 정제 방식**: allowed tag 목록, bare ampersand/angle bracket escape 규칙, 복구 실패 시 fallback, 품질 임계치를 정한다.
4. **표 정규화 깊이**: 모든 표를 cell-level로 만들지, 재무제표·핵심 사업 표를 우선 정규화하고 나머지는 직렬화할지 결정한다. MVP는 모든 표의 raw grid + 핵심 표의 numeric normalization을 권장한다.
5. **계정명 표준화**: `수익(매출액)`/`매출액`, `당기순이익`/`분기순이익` 동의어 사전과 원문 보존 방식을 정한다.
6. **단위 표준**: raw unit과 KRW scale을 함께 저장하고, 억원↔백만원 변환값을 사전 계산할지 질의 시 변환할지 정한다.
7. **기간 모델**: instant/duration, current/prior, annual/quarter-to-date/year-to-date를 공통 enum으로 확정한다.
8. **연결/별도 판정**: 제목 우선 규칙과 ACLASS fallback, unknown 처리 정책을 확정한다.
9. **첨부 범위**: 감사보고서/연결감사보고서를 본문과 함께 검색하되 낮은 우선순위를 줄지, 별도 collection으로 둘지 결정한다.
10. **이미지 확보**: DART에서 referenced JPG를 추가 수집할 권한·방법이 있는지 결정한다. 없으면 caption-only로 명시한다.
11. **chunk 크기 실험**: 섹션 기반 soft limit 후보를 2~3개로 만들고 실제 평가 질문으로 recall/근거 완결성을 비교한다.
12. **근거 locator**: XPath+element ordinal을 쓸지, 원문 byte/line offset까지 저장할지 결정한다. raw가 비정상 XML이므로 XPath만으로는 부족할 수 있다.
13. **중복 제거**: 본문 재무제표와 첨부 감사보고서의 같은 표를 중복으로 볼지, canonical/duplicate 관계만 표시할지 정한다.
14. **3개 `pdf+html` 예외**: 초기 MVP에서 제외 플래그를 명시할지, HTML 우선 fallback을 함께 구현할지 결정한다.

## 12. 미니 프로젝트 결론

정기공시는 공식 section 구조가 강해 섹션 기반 chunking에 적합하지만, 표와 세부 제목은 표현 방식이 일관되지 않다. 특히 수치 비교 정확도를 위해서는 `TABLE` 단위가 아니라 logical table 단위로 제목·단위·연결/별도·기간·다단 헤더·주석을 결합해야 한다.

다음 구현 단계의 우선순위는 다음이 적절하다.

1. raw 불변을 보장하는 보존적 XML 정제/복구 parser.
2. section path와 source locator 생성.
3. logical table 묶음과 span-aware grid 정규화.
4. correction version chain과 최신 유효본 정책.
5. 위 4개 샘플에 대한 golden fixture/회귀 테스트.

이 검증이 끝난 뒤에만 정기공시 전체로 범위를 넓히는 것이 안전하다.
