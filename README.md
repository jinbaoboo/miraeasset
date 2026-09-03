# 미래에셋증권 AI Festival 공시 Agent

주최 측이 제공한 4,204건의 공시 코퍼스만 사용해 정기·주요사항·거래소·지분공시를 구조화하고, 검색·수치 조회·계산·정정 이력·근거 기반 답변을 제공하는 프로젝트다. 원본 `raw` 파일은 읽기 전용이며 외부 뉴스나 웹 데이터는 답변 근거로 사용하지 않는다.
제출 패키지 구성과 평가자용 실행 순서는 [SUBMISSION.md](SUBMISSION.md)에 정리했다.

현재는 샘플 분석 단계가 아니라 전체 공시 4,204건을 구조화한 메인 프로젝트 단계다.
현재 개발 순서는 [메인 프로젝트 로드맵](docs/main_project_roadmap.md)을 기준으로 한다.

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
- 6대 핵심 질의 전용 경로: 연결 재무지표, 주요 투자계획, 기업 간 설비투자 비교,
  유상증자·CB·BW·EB 자금조달, 계약 체결–해지 연결, 연도별 핵심 사업 비교
- 주석 중간합계보다 주재무제표를 우선하고, 표가 나뉘어 저장된 경우 인접 단위를 복원
- 근거가 없거나 공시 범위 밖인 질문의 명시적 거절
- `claims`, `calculations`, `citations`, `limitations`, `confidence`, `validation` 기반 Evaluation Guardrail
- 필수 회사·기간·비교 대상·지표가 없을 때 reason code가 있는 구체적 역질문
- 자금조달 결정–정정과 계약 체결–정정–해지 lifecycle, 공시군 공통 정정 전후/current effective 조회
- 복수 재무지표와 설비투자 증감률+투자방향을 subtask로 나누는 복합 질의 planner
- 검색 점수 breakdown과 문서·섹션 중복을 줄이는 근거 재정렬, 재무지표 ontology
- 선택적 HyperCLOVA X 생성 어댑터와 `GET /answer` API
- 25개 수동 QA 회귀셋: 별칭·부문 수치·정정·사업내용·비교·복합·답변불가
- 40개 산업 확장 회귀셋: 10개 신규 기업, 3분기 3개월/누적, 주석/주재무제표 충돌, 정보 한계·보안
- 29개 운영 경계 회귀셋: 영문 대소문자·회사명 공백·운영 별칭·분기 표현, 상대 기간 역질문 reason code, 복합 질의, 보안·범위 제한
- 자체 개발 QA 100개: 신규 작성 `close` 50개·`open` 50개, 수치·단위·기간·scope·접수번호·서술 근거 동시 판정
- 강화형 API QA 100개: 영문 별칭·종목코드·축약 기간·지시문 교란을 포함한 `close` 50개·`open` 50개를 실제 HTTP로 질의하고 답변·접수번호·citation을 판정
- 메타모픽 API QA 100개: 제외 회사·연도가 섞인 의미 동일 질문 50쌍으로 정확한 대상 해석과 답변·인용 문서 일관성을 실제 HTTP로 판정

다른 LLM 공급자 연동은 코드에 포함하지 않았다. `HCX_*` 환경변수가 없으면 근거 기반 결정론 템플릿으로 동작한다.

## 데이터와 파생 저장소

입력은 `corpus/universe.csv`, `corpus/manifest.jsonl`, `corpus/raw/{periodic,major,exchange,holding}`이다. 파생 데이터는 `outputs/disclosures.db`에 생성되며 원본 경로에는 어떤 파일도 쓰지 않는다.

## 빠른 실행

테스트:

    python3 -m unittest discover -s tests -v
    make submission-check

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
    PYTHONPATH=. .venv/bin/python eval/evaluate_manual_qa.py --db outputs/disclosures.db
    make eval-operational DB=outputs/disclosures.db
    make eval-development DB=outputs/disclosures.db
    make eval-adversarial-http PYTHON=.venv/bin/python BASE_URL=http://127.0.0.1:8000
    make eval-metamorphic-http PYTHON=.venv/bin/python BASE_URL=http://127.0.0.1:8000
    PYTHONPATH=. .venv/bin/python validation/audit_source_locators.py \
      --db outputs/disclosures.db --data-root "/absolute/path/to/corpus"
    PYTHONPATH=. .venv/bin/python validation/validate_api_runtime.py \
      --base-url http://127.0.0.1:8000

Docker API 실행(구조화 DB는 read-only mount):

    docker build -t disclosure-agent .
    docker run --rm -p 8000:8000 \
      -v "$(pwd)/outputs/disclosures.db:/app/outputs/disclosures.db:ro" \
      --env-file .env disclosure-agent

## API 출력 계약

`GET /answer`는 답변과 함께 `claims`, `calculations`, `citations`, `limitations`, `confidence`, `validation`을 반환한다.
`retrieved_context`에는 레코드 종류·검색 내용·원문 위치를 가리키는 citation이 들어간다. `think_trace`는 비공개 내부 사고과정이 아니라 재현 가능한 실행 단계, 필터 및 도구 사용 요약이다. `debug=false`이면 원문 context와 trace만 숨길 수 있다.
`GET /metrics`에서는 지원 지표의 표준명·alias·선호 재무제표·부호 해석 정책을 확인할 수 있다.

## 품질 확인

    python3 -m validation.validate_cross_group_samples \
      --data-root "/absolute/path/to/corpus" \
      --output-dir validation/results

20개 교차 샘플은 annual/half/quarter, 각 정정 유형, PDF fallback, 복잡 표, strict XML 실패, 세 종류의 malformed 속성, 감사보고서 첨부, 여러 산업, 거래소 HTML, 일반·약식 지분공시를 포함한다.

전체 4,204건을 구조화한 결과 실패 문서는 0건이다. 단위·통합 테스트 121/121, 교차 샘플 검사 173/173, DB 무결성 검사 14/14를 통과했다. 수작업 base 37개에서 의미 보존 표현 변형을 생성한 robustness 평가는 150/150, 숫자 허용오차·단위·scope·기간·필수 근거·답변불가를 독립 검사하는 강한 골드 평가는 50/50 통과했다. 산업 확장·기간/출처 충돌·정보 한계 평가는 40/40, 운영 경계 평가는 29/29, 자체 작성 개발 QA는 `close` 50/50·`open` 50/50으로 총 100/100을 통과했다. 강화형 API QA도 실제 HTTP 전송으로 `close` 50/50·`open` 50/50, 총 100/100을 통과했다. 추가 메타모픽 API QA는 제외 조건을 섞은 `close` 50/50·`open` 50/50과 의미 동일 질문쌍 50/50을 통과해 답변의 접수번호 및 인용 문서 일관성까지 확인했다. DB-원문 감사는 30/30, API 런타임은 12/12를 통과했다. 재현 파일은 `eval/golden_questions.jsonl`, `eval/strong_gold_questions.jsonl`, `eval/cross_industry_audit_questions.jsonl`, `eval/operational_edge_questions.jsonl`, `eval/development_qa_100_questions.jsonl`, `eval/adversarial_qa_100_questions.jsonl`, `eval/metamorphic_qa_100_questions.jsonl`이다.

추가 수동 질문 25개도 `eval/manual_qa_questions.jsonl`로 편입해 25/25 통과했다. 다만 일부
질문은 정답값이 아니라 회사·근거 존재만 검사하므로, 통과율의 해석과 현재 부분 완료 영역은
[manual_qa_progress.md](docs/manual_qa_progress.md)에 구분해 두었다.

강한 골드 평가와 Open 답변 압축·사업 변화 비교 개선 내용은
[strong_gold_open_business.md](docs/strong_gold_open_business.md)에 정리했다.
산업 확장·원문 locator·API 동시성 검증은
[cross_industry_validation.md](docs/cross_industry_validation.md)에 정리했다.
입력 정규화·역질문·복합 질의·보안 경계 검증은
[operational_edge_validation.md](docs/operational_edge_validation.md)에 정리했다.
자체 작성 100문항의 구성·실행·개선 과정은
[development_qa_100_validation.md](docs/development_qa_100_validation.md)에 정리했다.
실제 HTTP로 수행한 강화형 100문항의 구성·실패 원인·개선 과정은
[adversarial_qa_100_validation.md](docs/adversarial_qa_100_validation.md)에 정리했다.
제외 조건과 의미 동일 질문쌍의 답변·인용 일관성 검증은
[metamorphic_qa_100_validation.md](docs/metamorphic_qa_100_validation.md)에 정리했다.
다음 개발 순서와 단계별 완료 기준은
[development_roadmap.md](docs/development_roadmap.md)에 정리했다.

## 중요한 한계

- 이미지 원본이 없으면 OCR하지 않고 파일명·캡션·문맥만 보존한다.
- 연결/별도 근거가 없으면 `unknown`을 유지한다.
- PDF/HTML로만 대체된 정기공시 3건은 PDF 본문과 페이지 위치를 검색용으로 보존하지만, 표·이미지·정정 전후는 구조화하지 않고 `warning`으로 명시한다.
- 비정기 정정 체인의 자동 연결은 모호한 경우 연결하지 않는다. 정정 전/후 자체는 보존된다.
- HyperCLOVA X 실호출은 발급받은 endpoint/key를 넣은 뒤 별도 통합 검증해야 한다.

설계 세부사항은 [architecture.md](docs/architecture.md), 정기공시 스키마는 [periodic_final_schema.md](docs/periodic_final_schema.md), API는 [api_spec.md](docs/api_spec.md)를 참고한다. 구현·검증 요약은 [implementation_report.md](docs/implementation_report.md), 제출용 내용 초안은 [technical_proposal.md](docs/technical_proposal.md), 사용자 권한이 필요한 마지막 작업은 [release_checklist.md](docs/release_checklist.md)에 분리했다.
