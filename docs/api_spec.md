# API 명세

## `GET /health`

구조화 데이터베이스 준비 상태를 반환한다.

- `200`: 프로세스 응답 성공
- `status=ok`: `DISCLOSURE_DB` 파일 존재
- `status=not_ready`: DB를 아직 생성하지 않음

## `GET /answer`

Query parameters:

| 필드 | 필수 | 설명 |
|---|---:|---|
| `question_id` | 예 | 평가 질문 식별자 |
| `question` | 예 | 한국어 자연어 질문 |
| `use_llm` | 아니오 | 기본 true. false면 HyperCLOVA X 없이 결정론 템플릿 사용 |

Response fields:

| 필드 | 설명 |
|---|---|
| `question_id` | 요청값 그대로 반환 |
| `question` | 요청 질문 |
| `retrieved_context` | 답변에 사용한 cell/chunk/table/event/correction과 citation |
| `think_trace` | query plan과 실행 단계 요약. 내부 chain-of-thought가 아님 |
| `answer` | 근거 범위 내 답변 또는 명시적 답변 불가 문구 |

Citation은 가능한 범위에서 회사명, 보고서명, 접수번호·접수일, section path, table title, row/column, 원문 표시값과 단위를 포함한다.

HTTP status:

- `200`: 답변 또는 근거 부족 거절도 정상 응답
- `422`: 필수 query parameter 누락
- `503`: 구조화 DB가 없음
- `500`: 예기치 못한 서버 오류. HyperCLOVA X 호출·citation 검증 실패는 로컬 근거 템플릿으로 안전하게 fallback

## HyperCLOVA X 환경변수

| 변수 | 의미 |
|---|---|
| `HCX_ENDPOINT` | 발급받은 chat completion endpoint |
| `HCX_API_KEY` | API 인증 키 |
| `HCX_APIGW_KEY` | 필요한 환경에서 API Gateway 키 |
| `HCX_REQUEST_ID_HEADER` | NCP 요청 ID 헤더명. 기본 `X-NCP-CLOVASTUDIO-REQUEST-ID` |
| `DISCLOSURE_DB` | SQLite 경로. 기본 `outputs/disclosures.db` |

다른 LLM endpoint나 fallback은 허용하지 않는다. 키가 없으면 로컬 템플릿 모드로 동작한다.

기계 판독용 응답 계약은 `schemas/answer_response.schema.json`에 있다.
