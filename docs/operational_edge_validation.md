# 운영 경계 질문 검증

## 목적

기존 골드 세트가 정답 수치·사업 내용·정정 이력을 넓게 다룬다면,
`eval/operational_edge_questions.jsonl`은 실제 API 입력에서 반복되는 경계 조건을 검증한다.
답변 문자열만 비교하지 않고 질문 분석 결과, 검증 action, reason code,
복합 질의 subtask 수, 접수번호·레코드 ID가 있는 citation을 함께 검사한다.

## 질문 구성

| 영역 | 건수 | 핵심 검증 |
|---|---:|---|
| 입력 정규화 | 7 | 영문 대소문자, 한글 회사명 공백, 종목코드, `1Q`·`첫 분기`·`1사분기`·`1/4분기` |
| 역질문 | 6 | 회사·기간·비교대상·지표·주제 누락의 `clarify` action과 reason code |
| 복합 질의 | 2 | 두 재무지표 분해, 모든 subtask 수치·근거 보존, 사용자 지표 표현 유지 |
| 보안·범위 제한 | 3 | 빈 질문, prompt injection/secret 요청, 목표주가 추천 |

## 발견한 문제와 개선

- `naver`, `hmm`, `jyp`가 기업으로 식별되지 않았다. 기업명·별칭·영문명을
  `casefold` 후 비교하도록 바꾸었다.
- `삼성 전자`처럼 공백이 들어간 회사명을 놓쳤다. 회사 식별용 문자열에서만 공백을
  제거해 정확 일치를 유지했다.
- `1사분기`를 연간 실적으로 잘못 해석했다. 분기 표현 규칙에 `n사분기`와
  `n/4분기`를 추가했다.
- 회사와 기간만 있는 질문이 임의 문단으로 흐르던 문제를 `missing_topic`
  역질문으로 바꾸었다. `재무 수치`처럼 범위만 있고 계정이 없는 질문은
  `missing_metric`으로 구분한다.
- 복합 질의 분해 시 `영업수익`을 `매출액`으로 바꾸던 문제를 수정해 사용자가
  입력한 지표 별칭을 subtask와 답변에 유지했다.

## 실행 결과

2026-09-02 기준 전체 `outputs/disclosures.db`에 대해 18/18을 통과했다.

```bash
make eval-operational DB=outputs/disclosures.db
```

상세 결과는 `eval/operational_edge_results.json`에 저장된다. 해당 파일은 질문별 check,
응답 지연, validation action, 인용 문서, 실제 답변을 포함한다.
