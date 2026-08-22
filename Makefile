PYTHON ?= python3
DB ?= outputs/disclosures.db
DATA_ROOT ?= /path/to/corpus

.PHONY: test validate build quality eval ask serve
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

ask:
	$(PYTHON) -m src.cli ask --db "$(DB)" "$(QUESTION)"

serve:
	DISCLOSURE_DB="$(DB)" uvicorn src.api.app:app --host 0.0.0.0 --port 8000
