# STEP 2. 정기공시 Parser 구현 및 1건 실행 결과

## 구현 파일

- `src/parser/periodic_parser.py`: manifest 조인, 문서/첨부 순회, section·chunk·table orchestration, JSON/JSONL 출력, 파일별 상태 로그.
- `src/parser/xml_recovery.py`: strict parse 우선, 실패 시 메모리상 bare ampersand와 비태그 angle text 복구. raw 파일은 쓰지 않는다.
- `src/parser/text_cleaner.py`: mixed content 텍스트, 공백, 굵은 SPAN 소제목 처리.
- `src/parser/table_parser.py`: TABLE-GROUP 및 인접 metadata/data/footnote 표 결합, span-aware grid, 다단 header, unit/scope/period, normalized cell.
- `src/parser/correction_parser.py`: 정정 대상·최초제출일, before/after, version chain과 current effective 의미 처리.

## 실행 명령

삼성전자 2023년 1분기보고서 1건만 실행했다. 첨부가 없는 문서이며 전체 corpus는 실행하지 않았다.

```bash
PYTHONPATH=. python3 -m src.parser.periodic_parser \
  --data-root "/Users/kimjinho/Downloads/3.공시/corpus" \
  --doc-id periodic_20230515002335 \
  --output-root outputs/step2_sample_final \
  --no-attachments
```

## 출력 요약

```json
{
  "doc_id": "periodic_20230515002335",
  "status": "warning",
  "record_counts": {
    "sections": 137,
    "text_chunks": 106,
    "logical_tables": 356,
    "table_cells": 20312,
    "corrections": 0,
    "images": 2
  }
}
```

`warning`은 실패가 아니다. main XML은 strict parse에 실패했지만 메모리 복구 후 구조화에 성공했다.

- bare ampersand 복구: 245개
- angle text 복구: 10개
- 실제 asset이 없는 이미지 참조: 2개
- failed: 0개

## 원문 대조 결과

연결 손익계산서는 다음과 같이 복원됐다.

```json
{
  "table_title": "연결 손익계산서",
  "section_path": ["III. 재무에 관한 사항", "2. 연결재무제표"],
  "unit": {"raw": "(단위 : 백만원)", "currency": "KRW", "scale": 1000000},
  "scope": "consolidated",
  "columns": [
    {"header_path": ["제 55 기 1분기", "3개월"]},
    {"header_path": ["제 55 기 1분기", "누적"]},
    {"header_path": ["제 54 기 1분기", "3개월"]},
    {"header_path": ["제 54 기 1분기", "누적"]}
  ]
}
```

영업이익 current YTD cell:

```json
{
  "row_label": "영업이익",
  "column_path": ["제 55 기 1분기", "누적"],
  "original_text": "640,178",
  "numeric_value": 640178,
  "unit": {"raw": "(단위 : 백만원)", "scale": 1000000},
  "period": {"start_date": "2023-01-01", "end_date": "2023-03-31", "aggregation": "ytd"},
  "scope": "consolidated"
}
```

표시값과 행·열·기간·단위·scope가 함께 유지됐으며 `640,178`은 main XML 원문과 일치한다.

## 출력 파일

```text
outputs/step2_sample_final/00126380/20230515002335/
├─ document.json
├─ sections.jsonl
├─ text_chunks.jsonl
├─ tables.jsonl
├─ cells.jsonl
├─ corrections.jsonl
├─ images.jsonl
└─ parse_log.json
```

기존 출력 디렉터리는 기본적으로 교체하지 않는다. 의도적으로 재생성할 때만 `--overwrite`를 사용한다.
