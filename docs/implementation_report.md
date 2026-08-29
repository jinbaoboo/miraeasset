# 공시 Agent 로컬 구현 완료 보고서

작성 기준일: 2026-08-29

## 완료 범위

주최 측 제공 코퍼스 4,204건을 원본 수정 없이 구조화하고, 검색·정형 수치 조회·정정 이력·계산·근거 인용 답변을 하나의 로컬 애플리케이션으로 구현했다.

- 정기공시 1,054건: XML 복구, section hierarchy, 본문 chunk, logical table, normalized cell, 연결/별도·기간·단위, 정정 정보
- 주요사항공시 598건: 문서 구조와 정형 이벤트 필드
- 거래소공시 1,469건: HTML grid와 정형 이벤트 필드
- 지분공시 1,083건: 보유 현황·관계 표 구조화와 검색용 파생 텍스트의 개인 식별 패턴 마스킹
- SQLite/FTS5 기반 저장·검색, `Decimal` 기반 계산, 최신 유효 정정본 우선 검색
- 질문 분석, 공시군 라우팅, 근거 부족·범위 밖 질문 거절, 정확한 원문 locator 인용
- 6대 핵심 질의 전용 실행 경로: 연결 재무지표, 주요 투자계획, 설비투자 비교,
  자금조달 유형별 집계, 계약 체결–해지 연결, 사업보고서 연도 비교
- 주장·계산·인용·한계·신뢰도·검증 결과를 반환하는 Evaluation Guardrail
- 자금조달 결정–정정 체인, 계약 체결–정정–해지 체인, 공시군 공통 정정 전후/current effective 조회
- HyperCLOVA X용 어댑터와 미설정 시 결정론적 답변 fallback(실 endpoint/key 통합은 미실시)
- FastAPI `GET /health`, `GET /answer`와 로컬 CLI

외부 뉴스·웹 정보와 다른 LLM은 사용하지 않는다. Vector DB와 임베딩은 MVP에 넣지 않았으며, 정확한 메타데이터 필터와 FTS5/정형 조회를 우선했다.

## 전체 코퍼스 결과

| 항목 | 수량 |
|---|---:|
| 기업 | 70 |
| 문서 | 4,204 |
| section | 163,152 |
| text chunk | 144,829 |
| logical table | 553,774 |
| normalized cell | 30,140,784 |
| correction | 1,002 |
| correction item | 4,948 |
| event | 3,150 |
| event field | 1,300,351 |

문서 상태는 `success` 2,433건, `warning` 1,771건, `failed` 0건이다. 모든 정기공시가 warning인 이유는 원문에 bare ampersand·angle text 또는 실제 파일이 없는 이미지 참조가 빈번하기 때문이다. 복구는 메모리에서만 수행했고 원본 파일과 hash는 변경하지 않았다.

PDF/HTML만 제공된 정기공시 3건은 페이지별 텍스트와 locator를 보존했다. 실제 표·이미지·정정 전후를 안전하게 복원할 수 없으므로 구조화하지 않고 경고로 남겼다.

## 검증 결과

| 검증 | 결과 |
|---|---:|
| 단위·통합 테스트 | 92 / 92 통과 |
| 20개 교차 샘플 수동·자동 대조 | 173 / 173 통과 |
| DB 무결성·참조·FTS·PII 검사 | 14 / 14 통과 |
| Golden/robustness 질문 평가 | 150 / 150 통과(base 37개 + 의미 보존 변형) |
| Strong gold 질문 평가 | 50 / 50 통과(숫자 허용오차·단위·scope·기간·필수 근거·답변불가) |
| 산업 확장·기간/출처 충돌·정보 한계 | 40 / 40 통과 |
| DB-원문 locator 감사 | 30 / 30 통과 |
| Golden 응답 지연 | median 325.84 ms, p95 4,448.27 ms, max 5,871.31 ms |
| Manual QA | 25 / 25 통과(수치·정정·사업·비교·복합·답변불가) |
| API 런타임 | 12 / 12 통과(정상·422·prompt injection·6개 동시 질의) |

교차 샘플은 annual/half/quarter, 정정 annual/half/quarter, 복잡 표, rowspan/colspan, strict XML 실패, 감사보고서 첨부, PDF fallback, 서로 다른 산업과 네 공시군을 포함한다. DB 검사에서는 manifest와 문서 수 일치, `PRAGMA integrity_check`, orphan 부재, FTS 레코드 수 일치, 최신 정정본 유일성, 검색 파생 필드의 민감 패턴 부재, 원본 경로 존재 여부를 검사했다.

## 주요 설계 결정

1. 검색용 `text`와 계산용 `rows/cells`를 병행한다. 숫자는 행·열·기간·단위·scope·원문 문자열을 함께 보존한다.
2. 표는 단일 `TABLE`이 아니라 제목·단위·`TABLE-GROUP`·주석을 묶은 logical table로 저장한다.
3. 연결/별도는 표 제목, 주변 section, `ACLASS`, 기타 힌트 순으로 판단하며 충돌하거나 근거가 없으면 `unknown`이다.
4. 정정 전/후를 모두 보존하고, 문서 버전과 `current_effective_value`를 별도로 둔다. 모호한 비정기 정정 체인은 억지로 연결하지 않는다.
5. 수치 계산은 `Decimal`을 사용하고 단위·통화·scope가 호환되지 않거나 값이 없으면 계산을 거절한다.
6. LLM은 검색된 근거를 문장으로 정리하는 선택 계층이다. 정형 수치 답변은 결정론적 경로를 우선하고, 생성 결과의 citation을 검증한다.
7. 자금조달 예정 납입일은 완료 증거가 아니다. 현재 코퍼스에 후속 완료 공시가 없으면 계획·결정 금액과 실제 완료액을 분리한다.
8. 계약 정정본은 원계약 수에 중복 포함하지 않고, 관련공시일·계약명·상대방·기간으로 해지를 연결한다. 동점이면 연결하지 않는다.
9. 사업 변화는 사업 개요·제품·부문/매출·신규사업·전략/기술·R&D·투자·시장 변화의 동일 분류로 비교한다.

## 재현 방법

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m src.pipeline.build_corpus \
  --data-root "/absolute/path/to/corpus" \
  --db outputs/disclosures.db
.venv/bin/python -m validation.validate_database \
  --db outputs/disclosures.db \
  --data-root "/absolute/path/to/corpus"
.venv/bin/python -m eval.evaluate_agent --db outputs/disclosures.db
DISCLOSURE_DB=outputs/disclosures.db \
  .venv/bin/uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

배치 작업은 문서별 transaction/checkpoint를 사용하므로 중단 후 재실행할 수 있다. 생성 DB는 약 30GB로 Git에 포함하지 않는다.

## 알려진 한계와 남은 사용자 작업

로컬 환경에는 Docker 실행 파일이 없어 Docker image build 자체는 검증하지 못했다. Dockerfile은 제공되며 Docker가 설치된 환경에서 마지막 실행 검증이 필요하다.

다음 세 범주는 자격증명·의사결정·외부 권한 없이는 완료할 수 없다.

1. 발급된 HyperCLOVA X/NCP endpoint와 secret을 넣은 실제 통합 시험
2. 정정 latest policy, PDF text-only fallback, SQLite/FTS5 MVP, 계산 거절 정책, API 계약의 최종 승인
3. 대회 GitHub Organization 반영, 배포 endpoint 개설 및 최종 제출

세부 체크 항목은 `docs/release_checklist.md`에 있다.
