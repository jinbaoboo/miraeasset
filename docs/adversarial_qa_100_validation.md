# 강화형 QA 100문항 실제 API 검증

## 목적

기존 자체 개발 100문항과 별도로, 표현 교란과 근거 제약을 강화한 신규 질문 100개를
`close` 50개와 `open` 50개로 구성했다. 평가는 에이전트를 파이썬에서 직접 호출하는
방식이 아니라 실행 중인 FastAPI의 `GET /answer`를 실제 HTTP로 호출한다.

## 질문 설계

- `close` 50개: 재무 수치, 연결/별도 범위, 분기 3개월·누적, 기본 재무제표와 주석 충돌,
  정정 전후, 비교·비율을 검사한다.
- `open` 50개: 사업 포트폴리오, 제품·서비스, 전략, 산업별 용어와 공시 문장 기반 요약을
  검사한다.
- 영문 회사명, 종목코드, 회사명 띄어쓰기, `'25 Q1`·`'25 Q3` 표기와 지시문 래퍼를
  섞어 질의 분석의 강건성을 높였다.
- 모든 문항에서 예상 회사·기간·지표·검증 action을 판정하고, 수치형은 값·단위·scope,
  서술형은 필수 근거 표현을 추가로 판정한다.
- 답변 안의 접수번호와 구조화 citation을 모두 요구한다. citation은 `doc_id`, `corp_name`,
  `report_nm`, `rcept_no`, `record_id`, `kind`가 비어 있지 않아야 한다.
- 답변 상한은 `close` 800자·10줄, `open` 900자·8줄이다.

질문은 `eval/build_adversarial_qa_100.py`가 결정론적으로 생성한다. 생성 결과는 100개 모두
고유하며 `eval/adversarial_qa_100_questions.jsonl`에 보존한다.

## 실제 API 실행

먼저 최신 코드로 API를 실행한다.

```bash
DISCLOSURE_DB=outputs/disclosures.db \
  .venv/bin/uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 다음 명령을 실행한다.

```bash
make eval-adversarial-http \
  PYTHON=.venv/bin/python \
  BASE_URL=http://127.0.0.1:8000
```

평가기는 각 문항을 `use_llm=false`, `debug=true`로 전송한 뒤 실제 JSON 응답의 답변,
query plan, validation, citation, retrieved context를 판정한다. 결과 파일의 `transport`가
`http`이므로 직접 함수 호출 결과와 구분할 수 있다.

## 발견 사항과 개선

최초 실제 HTTP 실행은 92/100(`close` 46/50, `open` 46/50)이었다.

- `Samsung Biologics`, `HD Hyundai Electric`이 넓은 `samsung`, `hyundai` 별칭 때문에
  같은 그룹의 다른 회사와 동시에 매칭됐다. 모호한 단일 단어 별칭을 제거하고 명시적인
  영문 회사 별칭을 추가했다.
- 평가 지시문이 사업 검색 핵심어에 섞였다. 회사명·종목코드·기간·평가용 래퍼를 제거하는
  focus-token 정규화를 검색과 문장 선택에 공통 적용했다.
- 현대자동차, 삼성전자, HMM, CJ제일제당의 사업 답변에서 요구한 부문·제품·전략 문구가
  누락됐다. 사업 topic 다양성과 실제 전략 문구가 풍부한 근거를 우선하도록 순위를 조정했다.
- 질의의 `'25`, `’26` 축약 연도를 2025년, 2026년으로 정규화했다.

## 최종 결과

최신 코드로 서버를 재기동한 뒤 같은 100문항을 다시 실제 HTTP로 전송한 결과는 다음과 같다.

| 구분 | 결과 |
|---|---:|
| `close` | 50/50 |
| `open` | 50/50 |
| 전체 | 100/100 |
| 전송 방식 | `http` |

질문별 실제 답변·판정 check·지연시간은 `eval/adversarial_qa_100_results.json`에 보존한다.
전체 단위·통합 테스트 117/117, strong gold 50/50, 산업 확장 40/40, 운영 경계
29/29, 기존 자체 개발 QA 100/100도 함께 재실행해 회귀가 없음을 확인했다.
