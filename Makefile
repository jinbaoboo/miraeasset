PYTHON ?= python3
DB ?= outputs/disclosures.db
DATA_ROOT ?= /path/to/corpus
BASE_URL ?= http://127.0.0.1:8000

.PHONY: test validate build quality eval eval-operational eval-development eval-adversarial-http eval-metamorphic-http eval-lifecycle-http eval-composite-http eval-hyperclova-http eval-safety-http eval-noisy-http eval-roadmap-http submission-check audit-source validate-api ask serve
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

eval-metamorphic-http:
	$(PYTHON) eval/build_metamorphic_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 \
		--questions eval/metamorphic_qa_100_questions.jsonl \
		--output eval/metamorphic_qa_100_results.json

eval-lifecycle-http:
	$(PYTHON) eval/build_lifecycle_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 \
		--questions eval/lifecycle_qa_100_questions.jsonl \
		--output eval/lifecycle_qa_100_results.json

eval-composite-http:
	$(PYTHON) eval/build_composite_calculation_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 \
		--questions eval/composite_calculation_qa_100_questions.jsonl \
		--output eval/composite_calculation_qa_100_results.json

eval-hyperclova-http:
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 --use-llm \
		--questions eval/composite_calculation_qa_100_questions.jsonl \
		--output eval/hyperclova_composite_qa_100_results.json

eval-safety-http:
	$(PYTHON) eval/build_unanswerable_security_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 \
		--questions eval/unanswerable_security_qa_100_questions.jsonl \
		--output eval/unanswerable_security_qa_100_results.json

eval-noisy-http:
	$(PYTHON) eval/build_noisy_language_qa_100.py
	PYTHONPATH=. $(PYTHON) eval/evaluate_manual_qa.py --base-url "$(BASE_URL)" --workers 4 \
		--questions eval/noisy_language_qa_100_questions.jsonl \
		--output eval/noisy_language_qa_100_results.json

eval-roadmap-http: eval-lifecycle-http eval-composite-http eval-safety-http eval-noisy-http

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
