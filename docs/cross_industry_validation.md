# 산업 확장·원문·API 추가 검증

기준일: 2026-08-29

## 목적

기존 골드에 많이 등장한 자동차·IT 기업을 벗어나 서로 다른 산업의 미사용 기업에서도 검색·수치·Open 요약이 유지되는지 검증했다. 또한 DB의 citation이 실제 raw XML로 연결되는지와 API가 동시 요청·오류 입력·프롬프트 공격에 안전한지를 따로 확인했다.

## 산업 확장 평가셋

`eval/cross_industry_audit_questions.jsonl`은 40문항으로 구성된다.

| 유형 | 문항 | 검증 내용 |
|---|---:|---|
| Close | 20 | 10개 기업의 2025년 1분기 연결 주재무제표 수치·단위·기간·공시 |
| Open | 10 | 해운, 바이오, 식품·물류, 통신, 게임, 철강, 부품, 건설, 전력기기 사업 요약 |
| 기간 충돌 | 4 | 3분기 `3개월`과 `누적` 열의 정확한 분리 |
| 출처 충돌 | 1 | 주석 중간합계보다 연결 주재무제표 우선 |
| 정보 한계·보안 | 5 | 기간 누락, 미등록 기업, 빈 질문, 미래 예측, 프롬프트 공격 |

최초 실행은 39/40이었다. 셀트리온 공시 근거에 바이오시밀러 내용이 있었지만 최종 사업 taxonomy에서 누락된 문제를 수정했다. 최종 결과는 40/40이다.

## 발견한 오류와 개선

1. 소득 지표의 선호 재무제표에 `포괄손익계산서`가 빠져 POSCO홀딩스의 주석 중간합계가 선택됐다. 매출·영업이익·순이익·매출총이익에 `comprehensive_income_statement`를 추가했다.
2. 3분기 재무제표에 `3개월`과 `누적` 열이 함께 있어도 질문의 집계 기준을 반영하지 못했다. Query plan에 `period_aggregation` (`three_month`/`ytd`)을 추가하고 일치 열에 강한 점수를 주었다.
3. `ICT`가 전력제어 제품에 포함됐다는 이유로 HD현대일렉트릭을 통신사로 분류했다. 통신사 전용 topic을 기업 업종 힌트로 제한하고 `전력기기`를 추가했다.
4. HMM의 전략 요약에 판매조직·판매경로·가격정책 문단이 들어갔다. 해당 표현을 전략 문장 후보에서 감점해 프리미어 얼라이언스·노선 다각화가 선택되게 했다.
5. 현대차 `차량부문` 질문에 기타부문 종속사 문장이 선택됐다. 부문 마커가 있으면 해당 범위를 먼저 요약하도록 제한했다.
6. 보안 공격 거절이 일반 정보 한계와 같은 `limit`으로 표시됐다. 프롬프트·자격증명 요청은 `blocked`로 분리했다.

## DB-원문 감사

`validation/audit_source_locators.py`는 30개 정기공시를 결정론적으로 선정해 DB와 raw XML을 대조한다.

- 15개 기업
- annual 10건, half 6건, quarter 14건
- 정정공시 9건
- 원문 파일, Cell·Table locator, columns/rows, 행·열·단위·scope, `original_text` 원문 일치 검사

결과는 30/30 통과이며 raw 디렉터리는 읽기 전용으로 사용했다.

## API 런타임 검증

`validation/validate_api_runtime.py`는 실제 HTTP API를 대상으로 다음 12개 검사를 실행한다.

- health와 지표 카탈로그
- 수치·접수번호가 있는 정상 답변
- `debug=false` context/trace 비공개
- 필수 파라미터 누락, 빈 질문, 길이 초과 질문·ID의 HTTP 422
- 프롬프트 공격 `blocked`와 secret 비노출
- 6개 동시 질의의 성공과 답변 결정성

결과는 12/12 통과다. Swagger UI는 `http://localhost:8000/docs`에서 확인할 수 있다.

## 최종 회귀 결과

| 검증 | 결과 |
|---|---:|
| 단위·통합 테스트 | 92/92 |
| 강한 골드 | 50/50 |
| 생성 Close/Open | 20/20 |
| 수동 QA | 25/25 |
| 강건성 변형 | 150/150 |
| 산업 확장·충돌·한계 | 40/40 |
| DB-원문 감사 | 30/30 |
| API 런타임 | 12/12 |

## 재현 명령

```bash
PYTHONPATH=. .venv/bin/python eval/evaluate_manual_qa.py \
  --db outputs/disclosures.db \
  --questions eval/cross_industry_audit_questions.jsonl \
  --output eval/cross_industry_audit_results.json

PYTHONPATH=. .venv/bin/python validation/audit_source_locators.py \
  --db outputs/disclosures.db \
  --data-root /absolute/path/to/corpus

PYTHONPATH=. .venv/bin/python validation/validate_api_runtime.py \
  --base-url http://127.0.0.1:8000 --concurrency 6
```
