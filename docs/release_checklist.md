# 최종 배포·제출 체크리스트

로컬 구현과 검증 이후, 권한·자격증명·최종 의사결정이 필요한 세 항목만 남긴다.

## 1. HyperCLOVA X / NCP 실연동

- [ ] 대회에서 허용된 HyperCLOVA X 모델과 endpoint를 확정한다.
- [ ] 배포 환경 secret manager에 `HCX_ENDPOINT`, `HCX_API_KEY`, 필요 시 `HCX_APIGW_KEY`를 주입한다.
- [ ] secret가 repository, Docker image, 로그, API 응답에 노출되지 않음을 확인한다.
- [ ] 실제 응답 JSON 형식, timeout, rate limit, 한글 품질을 staging에서 확인한다.
- [ ] 인용 번호 없음/범위 초과, 401/429/5xx, timeout 시 결정론적 fallback을 확인한다.
- [ ] 다른 LLM 연동 또는 외부 사실 데이터 호출이 없음을 최종 검색한다.

## 2. 중요 설계 최종 승인

- [ ] 정정 체인의 latest policy와 모호한 비정기 정정 미연결 policy를 승인한다.
- [ ] PDF fallback 3건을 text-only warning으로 제공하는 것을 승인한다.
- [ ] SQLite/FTS5 MVP의 디스크 크기·응답 속도와 vector DB 미사용을 승인한다.
- [ ] 계산 시 단위·통화·scope 불일치를 거절하는 정책을 승인한다.
- [ ] API의 `retrieved_context` 배열과 요약된 `think_trace` object 형식을 평가 클라이언트와 대조한다.

## 3. GitHub Organization 및 최종 제출

- [ ] 주최 측 Private Repository·branch·팀 권한을 확인한다.
- [ ] 대용량 `outputs/disclosures.db`를 Git에 커밋하지 않고, 재생성 명령 또는 허용된 다운로드 링크를 제공한다.
- [ ] README 의 clean environment 설치·DB build·API 실행 명령을 새 환경에서 재현한다.
- [ ] 기술 제안서를 최종 제출 형식으로 변환하고 표·도식·페이지 레이아웃을 검수한다.
- [ ] 평가용 HTTPS endpoint에서 `/health`, `/answer`, 422, 503, timeout/fallback을 확인한다.
- [ ] 마감 시각 전 최종 commit SHA·image digest·endpoint·API 명세를 기록하고 변경을 중지한다.

## 최종 로컬 검증 명령

```bash
python3 -m unittest discover -s tests -v
python3 -m validation.validate_cross_group_samples --data-root "/absolute/path/to/corpus"
python3 -m eval.evaluate_agent --db outputs/disclosures.db
python3 -m src.pipeline.report_quality --db outputs/disclosures.db
DISCLOSURE_DB=outputs/disclosures.db uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```
