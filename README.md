# 미래에셋증권 AI Festival 공시 Agent

주최 측이 제공한 4,204건의 공시 코퍼스만 사용해 정기·주요사항·거래소·지분공시를 구조화하고, 검색·수치 조회·계산·정정 이력·근거 기반 답변을 제공하는 프로젝트다. 원본 `raw` 파일은 읽기 전용이며 외부 뉴스나 웹 데이터는 답변 근거로 사용하지 않는다.

## 현재 구현 범위

- DART XML 메모리 복구: bare `&`, 비태그 `<...>`, 잘못된 제어문자
- XML이 없는 `pdf+html` 3건의 페이지 단위 본문 fallback(표 수치는 무리하게 구조화하지 않음)
- `SECTION-1..6`, `LIBRARY`, `TITLE`, 굵은 `SPAN` 계층 추출
- `TABLE-GROUP`, `TH/TD/TE/TU`, `ROWSPAN/COLSPAN`, 다단 헤더, 단위·기간·연결/별도 추론
- 확장자가 XML인 거래소 HTML 폼 전용 파서와 정형 이벤트 필드
- 정정 전/후와 최신 유효본용 버전 필드
- 지분공시 검색 인덱스의 주민번호·사업자번호·전화·이메일 최소 마스킹
- SQLite 정규화 저장, FTS5 본문·표·이벤트 검색, 정형 숫자 조회
- 누락값을 0으로 만들지 않는 `Decimal` 계산 도구
- 근거가 없거나 공시 범위 밖인 질문의 명시적 거절
- 선택적 HyperCLOVA X 생성 어댑터와 `GET /answer` API

다른 LLM 공급자 연동은 코드에 포함하지 않았다. `HCX_*` 환경변수가 없으면 근거 기반 결정론 템플릿으로 동작한다.

## 데이터와 파생 저장소

입력은 `corpus/universe.csv`, `corpus/manifest.jsonl`, `corpus/raw/{periodic,major,exchange,holding}`이다. 파생 데이터는 `outputs/disclosures.db`에 생성되며 원본 경로에는 어떤 파일도 쓰지 않는다.

## 빠른 실행

테스트:

    python3 -m unittest discover -s tests -v

전체 또는 공시군별 구조화:

    python3 -m src.pipeline.build_corpus \
      --data-root "/absolute/path/to/corpus" \
      --db outputs/disclosures.db \
      --groups major exchange holding

    python3 -m src.pipeline.build_corpus \
      --data-root "/absolute/path/to/corpus" \
      --db outputs/disclosures.db \
      --groups periodic

배치는 문서별 트랜잭션과 체크포인트를 사용한다. 중단 후 같은 명령을 실행하면 완료 문서는 건너뛴다. 특정 샘플만 처리하려면 `--doc-id`를 반복해서 사용한다.

API 실행:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    DISCLOSURE_DB=outputs/disclosures.db .venv/bin/uvicorn src.api.app:app --host 0.0.0.0 --port 8000
    curl -G http://localhost:8000/answer \
      --data-urlencode 'question_id=q1' \
      --data-urlencode 'question=삼성전자 2023년 1분기 연결 영업이익은?' \
      --data-urlencode 'use_llm=false'

로컬 CLI 질의와 품질 보고서:

    .venv/bin/python -m src.cli ask --db outputs/disclosures.db \
      '삼성전자 2023년 1분기 연결 영업이익은?'
    .venv/bin/python -m src.pipeline.report_quality --db outputs/disclosures.db
    .venv/bin/python -m eval.evaluate_agent --db outputs/disclosures.db

Docker API 실행(구조화 DB는 read-only mount):

    docker build -t disclosure-agent .
    docker run --rm -p 8000:8000 \
      -v "$(pwd)/outputs/disclosures.db:/app/outputs/disclosures.db:ro" \
      --env-file .env disclosure-agent

## API 출력 계약

`GET /answer`는 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`를 반환한다. `retrieved_context`에는 레코드 종류·검색 내용·원문 위치를 가리키는 citation이 들어간다. `think_trace`는 비공개 내부 사고과정이 아니라 재현 가능한 실행 단계, 필터 및 도구 사용 요약이다.

## 품질 확인

    python3 -m validation.validate_cross_group_samples \
      --data-root "/absolute/path/to/corpus" \
      --output-dir validation/results

20개 교차 샘플은 annual/half/quarter, 각 정정 유형, PDF fallback, 복잡 표, strict XML 실패, 세 종류의 malformed 속성, 감사보고서 첨부, 여러 산업, 거래소 HTML, 일반·약식 지분공시를 포함한다.

전체 4,204건을 구조화한 결과 실패 문서는 0건이다. 단위 테스트 36/36, 교차 샘플 검사 173/173, DB 무결성 검사 14/14, golden 질문 평가 31/31을 통과했다. 전체 레코드 수와 경고 해석, 재현 절차는 [implementation_report.md](docs/implementation_report.md)에 정리했다.

## 중요한 한계

- 이미지 원본이 없으면 OCR하지 않고 파일명·캡션·문맥만 보존한다.
- 연결/별도 근거가 없으면 `unknown`을 유지한다.
- PDF/HTML로만 대체된 정기공시 3건은 PDF 본문과 페이지 위치를 검색용으로 보존하지만, 표·이미지·정정 전후는 구조화하지 않고 `warning`으로 명시한다.
- 비정기 정정 체인의 자동 연결은 모호한 경우 연결하지 않는다. 정정 전/후 자체는 보존된다.
- HyperCLOVA X 실호출은 발급받은 endpoint/key를 넣은 뒤 별도 통합 검증해야 한다.

설계 세부사항은 [architecture.md](docs/architecture.md), 정기공시 스키마는 [periodic_final_schema.md](docs/periodic_final_schema.md), API는 [api_spec.md](docs/api_spec.md)를 참고한다. 구현·검증 요약은 [implementation_report.md](docs/implementation_report.md), 제출용 내용 초안은 [technical_proposal.md](docs/technical_proposal.md), 사용자 권한이 필요한 마지막 작업은 [release_checklist.md](docs/release_checklist.md)에 분리했다.
