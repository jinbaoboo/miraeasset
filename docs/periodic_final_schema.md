# 정기공시 최종 데이터 스키마

## 1. 권장 전체 스키마

Parser의 논리 출력은 아래 7개 레코드 계층으로 분리한다.

```text
PeriodicParseResult
├─ document                 1개
├─ sections                 0..N
├─ text_chunks              0..N
├─ logical_tables           0..N
├─ table_cells              0..N
├─ corrections              0..N
└─ images / parse_log       0..N / 1개
```

모든 하위 레코드는 `source` 객체를 반복한다. 저장 공간보다 독립 검색, 필터링, 근거 표시와 JSONL 재처리 안정성을 우선한 결정이다.

### 공통 `source`

```json
{
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
  "file_path": "raw/periodic/삼성전자/20240312000736_annual_2023_12"
}
```

### document

```text
schema_version, source, document_name, document_acode, formula_version,
source_files[], attachments[], version, parse_summary, record_counts,
raw_sha256, created_at
```

### section

```text
section_id, source, source_file, source_file_role, parent_section_id,
level, title, title_source, section_path[], order, xml_path
```

### text chunk

```text
chunk_id, source, source_file, source_file_role, section_id,
section, subsection, section_path[], content_type, heading,
text, paragraph_count, related_table_ids[], source_locator
```

### logical table

```text
table_id, source, source_file, source_file_role, section_id,
section, subsection, section_path[], table_title, title_source,
unit, scope, statement_type, periods[], columns[], rows[],
normalized_cell_ids[], footnotes[], physical_tables[], search_text,
source_locator
```

### table cell

```text
cell_id, table_id, source, source_file, section_path[], row_index,
column_index, row_label, row_path[], column_label, column_path[],
original_text, value, value_type, numeric_value, unit, period,
scope, is_missing, parse_warnings[]
```

### correction information

```text
correction_id, source, version_role, original_doc_id,
correction_doc_id, supersedes_doc_id, superseded_by_doc_id,
is_latest_version, correction_date, original_filing_date,
target_document, correction_items[]
```

`correction_items[]`는 `item`, `reason`, `before`, `after`,
`current_effective_value`, `before_cell_ids`, `after_cell_ids`,
`source_locator`를 가진다.

## 2. 각 필드 설명

### 식별·출처

| 필드 | 설명 |
|---|---|
| `schema_version` | Parser 출력 계약 버전. 스키마 변경 시 증가한다. |
| `source` | manifest에서 가져온 공통 출처. XML 내부 회사명보다 manifest 값을 우선한다. |
| `source_file` | 실제 XML 파일명. |
| `source_file_role` | `main`, `audit_report`, `consolidated_audit_report`, `other_attachment`. |
| `source_locator` | `xml_path`, 문서 내 element/table ordinal, 가능하면 line을 담는 근거 위치. |
| `raw_sha256` | 원본을 변경하지 않았음을 검증하는 파일 hash. |

### 문서·버전

| 필드 | 설명 |
|---|---|
| `document_name`, `document_acode` | XML `DOCUMENT-NAME`과 `ACODE`. 정정 여부는 포함하지 않는다. |
| `formula_version` | `FORMULA-VERSION` 값과 `ADATE`. 양식 회귀 테스트에 사용한다. |
| `version_role` | `original` 또는 `correction`. |
| `original_doc_id` | 정정 대상 최초 문서. 알 수 없으면 `null`. |
| `supersedes_doc_id` | 바로 이전 유효 버전. 다중 정정을 지원한다. |
| `is_latest_version` | 동일 회사·유형·기준기간의 현재 유효본 여부. manifest 전체를 읽을 때만 확정 가능하다. |

### 섹션·본문

| 필드 | 설명 |
|---|---|
| `section_id` | 문서와 파일 역할을 포함한 안정적 ID. |
| `parent_section_id` | 공식 또는 style-inferred 부모 섹션. |
| `level` | 공식 `SECTION-1/2/3` 또는 추론된 하위 수준. |
| `title_source` | `TITLE`, `bold_span`, `table_group_title`, `synthetic`. |
| `section_path` | 최상위부터 현재까지 제목 배열. 근거 header와 검색 필터에 사용한다. |
| `text` | 검색/RAG용 정리 텍스트. 원문 수치를 재계산하는 canonical 필드는 아니다. |

### 표

| 필드 | 설명 |
|---|---|
| `table_title` | TABLE-GROUP TITLE, 제목 표, 주변 제목에서 정한 logical table 제목. |
| `unit.raw` | 원문 단위 문자열. |
| `unit.currency` | `KRW`, `USD` 등. 알 수 없으면 `null`. |
| `unit.scale` | 원=1, 천원=1000, 백만원=1000000, 억원=100000000. |
| `unit.quantity` | `money`, `shares`, `percent`, `count`, `mixed`, `unknown`. |
| `scope` | `consolidated`, `separate`, `unknown`. 추정 근거도 `scope_evidence`에 둔다. |
| `periods` | 실제 날짜, instant/duration, current/prior, quarter/ytd 의미. |
| `columns` | 다단 헤더를 `header_path`로 보존한 leaf column 정의. |
| `rows` | 행 번호, 행 라벨/path, 해당 cell ID 목록. |
| `normalized_cell_ids` | canonical cell 레코드 참조. |
| `footnotes` | 표 직후 `※`, `주)`, 부호 설명 등. |
| `search_text` | 회사·보고서·section·표 제목·단위·행/열/값을 합성한 검색용 텍스트. |

### cell

| 필드 | 설명 |
|---|---|
| `row_label`, `row_path` | 행 이름과 계층. 원문 들여쓰기를 복원할 수 없으면 label만 유지한다. |
| `column_label`, `column_path` | leaf label과 다단 헤더 전체 경로. |
| `original_text` | 셀 원문. 숫자 파싱과 무관하게 보존한다. |
| `value` | 정리된 표시값. |
| `numeric_value` | 계산 가능한 수치. `-`, 빈칸, 해당 없음은 임의로 0으로 만들지 않는다. |
| `value_type` | `number`, `percent`, `text`, `dash`, `empty`. |
| `period` | column이 가리키는 기간 객체 또는 `null`. |
| `unit`, `scope` | 표에서 상속하되 cell별 override가 있으면 cell 값을 우선한다. |

## 3. text chunk JSON 예시

```json
{
  "chunk_id": "periodic_20240312000736:main:text:000042",
  "source": {
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
    "file_path": "raw/periodic/삼성전자/20240312000736_annual_2023_12"
  },
  "source_file": "20240312000736.xml",
  "source_file_role": "main",
  "section_id": "periodic_20240312000736:main:section:II-6-rnd",
  "section": "II. 사업의 내용",
  "subsection": "6. 주요계약 및 연구개발활동",
  "section_path": [
    "II. 사업의 내용",
    "6. 주요계약 및 연구개발활동",
    "나. 연구개발활동의 개요 및 연구개발비용"
  ],
  "content_type": "text",
  "heading": "나. 연구개발활동의 개요 및 연구개발비용",
  "text": "삼성전자 | 사업보고서 (2023.12) | II. 사업의 내용 > 6. 주요계약 및 연구개발활동 > 나. 연구개발활동의 개요 및 연구개발비용\n2023년 연구개발비용은 28조 3,528억원이며 ...",
  "paragraph_count": 2,
  "related_table_ids": ["periodic_20240312000736:main:table:000031"],
  "source_locator": {"xml_path": "/DOCUMENT/BODY/SECTION-1[3]/LIBRARY/SECTION-2[6]/P[8]", "element_ordinal": 6236}
}
```

## 4. table JSON 예시

```json
{
  "table_id": "periodic_20230814002534:main:table:000012",
  "source": {"doc_id": "periodic_20230814002534", "corp_code": "00126380", "corp_name": "삼성전자", "listed_name": "삼성전자", "report_nm": "반기보고서 (2023.06)", "rcept_no": "20230814002534", "rcept_dt": "20230814", "doc_group": "periodic", "doc_subtype": "half", "base_year": 2023, "base_month": 6, "is_correction": false, "file_path": "raw/periodic/삼성전자/20230814002534_half_2023_06"},
  "section": "III. 재무에 관한 사항",
  "subsection": "2. 연결재무제표",
  "section_path": ["III. 재무에 관한 사항", "2. 연결재무제표", "연결 손익계산서"],
  "table_title": "연결 손익계산서",
  "title_source": "first_table_row",
  "unit": {"raw": "(단위 : 백만원)", "currency": "KRW", "scale": 1000000, "quantity": "money"},
  "scope": "consolidated",
  "scope_evidence": "table_title:연결 손익계산서",
  "periods": [
    {"period_id": "current_ytd", "label": "제55기 반기 누적", "start_date": "2023-01-01", "end_date": "2023-06-30", "period_type": "duration", "comparison_role": "current", "aggregation": "ytd"}
  ],
  "columns": [
    {"column_index": 2, "label": "누적", "header_path": ["제55기 반기", "누적"], "period_id": "current_ytd"}
  ],
  "rows": [
    {"row_index": 6, "label": "영업이익", "row_path": ["영업이익"], "cell_ids": ["periodic_20230814002534:main:table:000012:r6:c2"]}
  ],
  "normalized_cell_ids": ["periodic_20230814002534:main:table:000012:r6:c2"],
  "footnotes": [],
  "physical_tables": [{"ordinal": 1, "role": "metadata"}, {"ordinal": 2, "role": "data"}],
  "search_text": "삼성전자 반기보고서 2023.06 연결 손익계산서 단위 백만원 | 영업이익 | 제55기 반기 누적 | 1,308,725"
}
```

## 5. cell JSON 예시

```json
{
  "cell_id": "periodic_20230814002534:main:table:000012:r6:c2",
  "table_id": "periodic_20230814002534:main:table:000012",
  "source": {"doc_id": "periodic_20230814002534", "corp_code": "00126380", "corp_name": "삼성전자", "listed_name": "삼성전자", "report_nm": "반기보고서 (2023.06)", "rcept_no": "20230814002534", "rcept_dt": "20230814", "doc_group": "periodic", "doc_subtype": "half", "base_year": 2023, "base_month": 6, "is_correction": false, "file_path": "raw/periodic/삼성전자/20230814002534_half_2023_06"},
  "row_index": 6,
  "column_index": 2,
  "row_label": "영업이익",
  "row_path": ["영업이익"],
  "column_label": "누적",
  "column_path": ["제55기 반기", "누적"],
  "original_text": "1,308,725",
  "value": "1,308,725",
  "value_type": "number",
  "numeric_value": 1308725,
  "unit": {"raw": "(단위 : 백만원)", "currency": "KRW", "scale": 1000000, "quantity": "money"},
  "period": {"period_id": "current_ytd", "start_date": "2023-01-01", "end_date": "2023-06-30", "aggregation": "ytd"},
  "scope": "consolidated",
  "is_missing": false,
  "original_tag": "TD",
  "parse_warnings": []
}
```

## 6. correction JSON 예시

```json
{
  "correction_id": "periodic_20240329002895:correction:0001",
  "source": {"doc_id": "periodic_20240329002895", "corp_code": "00126371", "corp_name": "삼성전기", "listed_name": "삼성전기", "report_nm": "[기재정정]사업보고서 (2023.12)", "rcept_no": "20240329002895", "rcept_dt": "20240329", "doc_group": "periodic", "doc_subtype": "annual", "base_year": 2023, "base_month": 12, "is_correction": true, "file_path": "raw/periodic/삼성전기/20240329002895_annual_2023_12"},
  "version_role": "correction",
  "original_doc_id": "periodic_20240312000778",
  "correction_doc_id": "periodic_20240329002895",
  "supersedes_doc_id": "periodic_20240312000778",
  "superseded_by_doc_id": null,
  "is_latest_version": true,
  "correction_date": "2024-03-29",
  "original_filing_date": "2024-03-12",
  "target_document": "제51기 사업보고서",
  "correction_items": [
    {
      "item": "2-4. 연결 현금흐름표",
      "reason": "단순 기재오류",
      "before": {"original_text": "금융리스부채의 지급", "cell_ids": []},
      "after": {"original_text": "리스부채의 지급", "cell_ids": []},
      "current_effective_value": {"source": "after", "original_text": "리스부채의 지급"},
      "source_locator": {"correction_table_ordinal": 1, "row_index": 1}
    }
  ]
}
```

`before`는 역사적 근거, `after`는 해당 정정본의 변경값이다. 최신 정정본이면 `current_effective_value.source=after`로 둔다. 이후 정정이 존재하는 과거 정정본은 예전 `after`를 현재값이라고 오인하지 않도록 `source=superseded_by`, 최신 정정 `doc_id`, `original_text=null`로 둔다. 서로 다른 정정표의 항목을 안정적으로 매핑하는 규칙을 확정한 뒤에만 최신 문서의 실제 값을 역연결한다.

## 7. 파일 저장 구조 제안

```text
data/
├─ universe.csv
├─ manifest.jsonl
├─ raw/periodic/...                 # 읽기 전용 원본
└─ structured/periodic/
   └─ {corp_code}/
      └─ {rcept_no}/
         ├─ document.json
         ├─ sections.jsonl
         ├─ text_chunks.jsonl
         ├─ tables.jsonl
         ├─ cells.jsonl
         ├─ corrections.jsonl
         ├─ images.jsonl
         └─ parse_log.json
```

원본과 출력 root를 분리하고, 임시 디렉터리에 모두 쓴 뒤 성공 시 접수번호 디렉터리로 원자적 이동한다. 기존 출력은 기본적으로 덮어쓰지 않고 명시적 `--overwrite`에서만 교체한다. JSONL은 레코드 단위 재처리와 향후 DB 적재에 유리하다.

## 8. Parser 구현 전에 확정해야 할 사항

이번 구현에서는 아래 기본값을 사용하되 전체 일괄처리 전 평가 결과로 확정해야 한다.

1. 정정본 검색 정책: 원본 보존, 최신 유효본 기본 노출, 정정 diff 별도 검색.
2. version chain key: `corp_code + doc_subtype + base_year + base_month`, 접수일/접수번호 순.
3. strict XML 복구: 허용 DART 태그 allowlist 밖의 angle text escape, bare ampersand escape, 원본 hash와 repair log 보존.
4. 표 canonical 모델: `cells.jsonl`을 계산의 기준으로 하고 `tables.jsonl`은 구조와 참조를 보유.
5. 숫자 정책: 빈칸·dash·해당 없음은 null이며 0으로 변환하지 않음.
6. 단위 변환: 원문 수치와 scale을 저장하고 실제 KRW 환산은 질의/분석 계층에서 수행.
7. 기간 enum: `instant|duration`, `current|prior|unknown`, `three_month|ytd|annual|unknown`.
8. scope: 명시 근거가 없으면 `unknown`; 회사 유형을 이용한 추측 금지.
9. 첨부 XML: 같은 document 아래 `source_file_role`로 분리하고 main보다 낮은 검색 우선순위 부여.
10. 이미지: asset이 없으면 metadata만 저장하고 OCR 금지.
11. source locator: ElementTree가 line을 보존하지 않으므로 우선 xml path+ordinal을 사용하고, 필요 시 byte offset scanner 추가.
12. chunk soft limit: 현재 1,800자, 같은 section 내에서만 분할. 평가 질의로 조정.
13. logical table 병합: TABLE-GROUP 우선, standalone은 metadata→data→footnote 인접 패턴만 보수적으로 병합.
14. 비XML `pdf+html` 3건은 형식 라우터에서 PDF fallback으로 분기. 페이지 본문·locator는 보존하되 표·이미지·정정 전후는 안전하게 구조화할 수 없으므로 warning으로 명시.
