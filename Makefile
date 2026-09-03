PYTHON ?= python3
DB ?= outputs/disclosures.db
DATA_ROOT ?= /path/to/corpus
BASE_URL ?= http://127.0.0.1:8000

.PHONY: test validate build quality eval eval-operational eval-development eval-adversarial-http submission-check audit-source validate-api ask serve
test:
	$(PYTHON) -m unittest discover -s tests -v

validate:
	$(PYTHON) -m validation.validate_cross_group_samples --data-root "$(DATA_ROOT)"

build:
	$(PYTHON) -m src.pipeline.build_corpus --data-root "$(DATA_ROOT)" --db "$(DB)"

quality:
	$(PYTHON) -m src.pipeline.report_quality --db "$(DB)"
	$(PYTHON) -m validation.validate_database --db "$(DB)" --data-root "$(DATA_ROOT)"

eval:
	$(PYTHON) -m eval.evaluate_agent --db "$(DB)"

eval-operational:
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --db "$(DB)" \
		--questions eval/operational_edge_questions.jsonl \
		--output eval/operational_edge_results.json

eval-development:
	$(PYTHON) eval/build_development_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --db "$(DB)" \
		--questions eval/development_qa_100_questions.jsonl \
		--output eval/development_qa_100_results.json

eval-adversarial-http:
	$(PYTHON) eval/build_adversarial_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 \
		--questions eval/adversarial_qa_100_questions.jsonl \
		--output eval/adversarial_qa_100_results.json

submission-check:
	$(PYTHON) -m validation.validate_submission_package

audit-source:
	$(PYTHON) -m validation.audit_source_locators --db "$(DB)" --data-root "$(DATA_ROOT)"

validate-api:
	$(PYTHON) -m validation.validate_api_runtime --base-url http://127.0.0.1:8000

ask:
	$(PYTHON) -m src.cli ask --db "$(DB)" "$(QUESTION)"

serve:
	DISCLOSURE_DB="$(DB)" uvicorn src.api.app:app --host 0.0.0.0 --port 8000
