# HyperCLOVA X 실연결 및 QA 검증

최종 검증일: 2026-09-04

## 연동 구성

- 모델: `HCX-005`
- API: `https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005`
- 인증: `Authorization: Bearer` 방식
- 로컬 비밀정보: Git에서 제외된 `.env`에만 저장하고 파일 권한을 `600`으로 제한
- 재시도: HTTP 408·425·429·5xx 및 네트워크 오류를 최대 2회 지수 지연 재시도
- 요청 규격: v3의 `repetitionPenalty` 필드 사용

수치 조회·비교·계산은 원문 값과 단위를 바꾸지 않도록 결정론적 계산 경로를 유지한다.
서술형 생성은 최대 10개 근거를 사용하며, 모델이 반환한 모든 `[근거 n]`이 실제 전달 범위에
있는지 검사한다. 인용이 없거나 근거가 충분한데도 답변을 포기한 생성문은 기존 전문 답변으로
fallback한다. API 오류·timeout 때도 동일한 fallback을 사용한다.

## 실제 연결 확인

로컬 8001번 FastAPI 서버를 `.env`와 함께 실행하여 다음을 확인했다.

- `HCX-005` 최소 직접 호출 성공
- `/health` 정상
- `/answer?use_llm=true`에서 `hyperclova_x_grounded_generation` 실행 확인
- 생성 답변의 `[근거 n]`, response citation, 공시 접수번호 표시 확인
- 잘못된 인용과 불필요한 답변 포기 시 결정론적 답변 복구 확인
- 키 형태의 값이 `.env` 외 파일에 존재하지 않음을 검사

## 실제 HTTP 100문항 결과

`composite_calculation_qa_100_questions.jsonl`의 Close 50개와 Open 50개를 `workers=4`,
`use_llm=true`로 실제 API에 전송했다.

| 지표 | 결과 |
|---|---:|
| Close | 50/50 |
| Open | 50/50 |
| 전체 | 100/100 |
| HTTP median | 101.59 ms |
| HTTP p95 | 6,924.12 ms |
| HTTP p99 | 12,818.69 ms |
| HTTP max | 16,199.75 ms |
| 접수번호가 포함된 최종 답변 | 100/100 |
| `[근거 n]` 생성 형식이 최종 채택된 답변 | 6/100 |

모델 생성이 필요 없는 정형 질문이 절반 이상이어서 median은 낮게 유지된다. p95 이상은 실제
HyperCLOVA 생성 시간이 반영된 값이다. 생성 인용 검증을 통과하지 못한 경우에도 근거 기반 전문
답변을 반환하므로 전체 품질 게이트는 유지됐다. 원문 결과는
`eval/hyperclova_composite_qa_100_results.json`에 질문별로 보존한다.

## 재현

```bash
DISCLOSURE_DB=outputs/disclosures.db \
  .venv/bin/uvicorn src.api.app:app --env-file .env \
  --host 127.0.0.1 --port 8001

make eval-hyperclova-http \
  PYTHON=.venv/bin/python \
  BASE_URL=http://127.0.0.1:8001
```

`.env`에는 `HCX_ENDPOINT`, `HCX_API_KEY`, 선택적인 재시도 설정을 둔다. 실제 키는 문서·결과
JSON·Git 이력에 기록하지 않는다.
