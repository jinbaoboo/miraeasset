# 최종 제출 안내

이 저장소는 소스코드, 기술 제안서, API 명세, 재현·평가 도구를 포함한다.
원본 공시 코퍼스, 30GB 구조화 DB, 인증정보는 포함하지 않는다.

## 평가자 빠른 시작

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

코퍼스로 DB를 재생성하는 명령은 [DATA_GUIDE.md](DATA_GUIDE.md), API 실행 방법은
[README.md](README.md), 요청·응답 계약은 [docs/api_spec.md](docs/api_spec.md)를 따른다.

```bash
python3 -m src.pipeline.build_corpus \
  --data-root "/absolute/path/to/corpus" \
  --db outputs/disclosures.db

DISCLOSURE_DB=outputs/disclosures.db \
  uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

## 저장소 품질 게이트

```bash
make test
make submission-check
make eval-operational DB=outputs/disclosures.db
make eval-development DB=outputs/disclosures.db
make eval-adversarial-http PYTHON=.venv/bin/python BASE_URL=http://127.0.0.1:8000
make eval-metamorphic-http PYTHON=.venv/bin/python BASE_URL=http://127.0.0.1:8000
make eval-roadmap-http PYTHON=.venv/bin/python BASE_URL=http://127.0.0.1:8000
```

`submission-check`는 필수 제출 파일, Git에 추적된 DB·코퍼스·secret 파일,
50MB 초과 파일, `.env.example` secret 값을 검사한다.

## 핵심 제출물

- 기술 제안서: [docs/technical_proposal.md](docs/technical_proposal.md)
- API 명세: [docs/api_spec.md](docs/api_spec.md)
- API 응답 JSON Schema: [schemas/answer_response.schema.json](schemas/answer_response.schema.json)
- 구현·검증 보고서: [docs/implementation_report.md](docs/implementation_report.md)
- 자체 개발 QA 100문항 검증: [docs/development_qa_100_validation.md](docs/development_qa_100_validation.md)
- 강화형 QA 100문항 실제 API 검증: [docs/adversarial_qa_100_validation.md](docs/adversarial_qa_100_validation.md)
- 메타모픽 QA 100문항 실제 API 검증: [docs/metamorphic_qa_100_validation.md](docs/metamorphic_qa_100_validation.md)
- 발전 로드맵 1~4단계 QA 400문항 실제 API 검증: [docs/roadmap_qa_400_validation.md](docs/roadmap_qa_400_validation.md)
- 후속 개발 로드맵: [docs/development_roadmap.md](docs/development_roadmap.md)
- 배포 전 체크리스트: [docs/release_checklist.md](docs/release_checklist.md)
- 환경변수 키 예시: [.env.example](.env.example)

## 대용량 DB 제공 방식

기본 제출 방식은 `outputs/disclosures.db`를 Git에 포함하지 않고
[DATA_GUIDE.md](DATA_GUIDE.md)의 명령으로 재생성하는 것이다. 주최 측이 클라우드 링크를
허용하면 아래 `DB 다운로드 링크`를 제출 직전에만 추가한다.

## 제출 직전 확정 정보

아래 값은 외부 권한·배포 결과가 필요하므로 최종 제출 시점에 입력한다.
secret 자체는 절대 기록하지 않는다.

```text
팀명: [필수 입력]
주최 측 Private Repository URL: [필수 입력]
제출 Branch: main
최종 Commit SHA: [마감 직전 입력]
Docker Image Digest: [배포 후 입력]
평가용 HTTPS Endpoint: [배포 후 입력]
Health Check URL: [배포 후 입력]
DB 제공 방식: 코퍼스로부터 재생성
DB 다운로드 링크: [허용 시 입력]
기술 제안서 PDF 파일명: [PDF 변환 후 입력]
제출 완료 시각: [제출 후 입력]
담당자: [필수 입력]
```

## 외부 작업

다음은 이 저장소에서 자동으로 완료할 수 없는 항목이다.

- 주최 측 GitHub Organization Private Repository 권한 및 원격 URL 확정
- HyperCLOVA X/NCP secret manager 주입과 실호출 통합 검증
- 기술 제안서 PDF 변환·레이아웃 검수
- 평가용 HTTPS endpoint 배포와 2026-09-07~2026-09-20 운영
- 마감 전 최종 SHA·image digest·endpoint 기록 후 변경 중지
