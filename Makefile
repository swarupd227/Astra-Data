.DEFAULT_GOAL := help
SHELL := /bin/bash

GRAPH_SVC := services/graph-svc
ADAPTER_SDK := packages/adapter-sdk
ADAPTER_TABLEAU := packages/adapter-tableau
CONSOLE_WEB := services/console-web
PY ?= python

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install the adapter packages and graph-svc into the active environment
	$(PY) -m pip install -e "$(ADAPTER_SDK)[dev]"
	$(PY) -m pip install -e "$(ADAPTER_TABLEAU)[dev]"
	$(PY) -m pip install -e "$(GRAPH_SVC)[dev]"

.PHONY: dev-up
dev-up: ## Start PostgreSQL 16 with Apache AGE
	docker compose up -d postgres

.PHONY: dev-down
dev-down: ## Stop local services
	docker compose down

.PHONY: migrate
migrate: ## Apply pending migrations to the local database
	cd $(GRAPH_SVC) && $(PY) tools/migrate.py

.PHONY: migrate-status
migrate-status: ## Show applied and pending migrations
	cd $(GRAPH_SVC) && $(PY) tools/migrate.py --status

.PHONY: ontology
ontology: ## Regenerate docs/generated/ontology.md from the schema
	cd $(GRAPH_SVC) && $(PY) tools/ontology_check.py --generated --write

.PHONY: check
check: ## Drift guards: generated reference, specification conformance, migration coverage
	cd $(GRAPH_SVC) && $(PY) tools/ontology_check.py
	cd $(GRAPH_SVC) && $(PY) tools/migration_check.py
	cd $(GRAPH_SVC) && $(PY) tools/contract_check.py

.PHONY: lint
lint: ## Lint and type-check
	cd $(ADAPTER_SDK) && $(PY) -m ruff check .
	cd $(ADAPTER_SDK) && $(PY) -m mypy
	cd $(ADAPTER_TABLEAU) && $(PY) -m ruff check .
	cd $(ADAPTER_TABLEAU) && $(PY) -m mypy
	cd $(GRAPH_SVC) && $(PY) -m ruff check .
	cd $(GRAPH_SVC) && $(PY) -m mypy

.PHONY: test
test: ## Unit tests (no database)
	cd $(ADAPTER_SDK) && $(PY) -m pytest -q
	cd $(ADAPTER_TABLEAU) && $(PY) -m pytest -q
	cd $(GRAPH_SVC) && $(PY) -m pytest -m "not integration" -q

.PHONY: conformance
conformance: ## Run the adapter conformance suite, in process and over the RPC
	$(PY) -m astra_adapter.cli conformance --adapter fake
	$(PY) -m astra_adapter.cli conformance --adapter fake --remote --out conformance-report.json

.PHONY: tableau-golden
tableau-golden: ## Serve the golden Tableau deployment on :8099 (§6.3's corpus)
	$(PY) -m astra_adapter_tableau.golden_serve --port 8099

.PHONY: conformance-verify
conformance-verify: ## Check a signed conformance report's hash and signature
	$(PY) -m astra_adapter.cli verify conformance-report.json

.PHONY: adapter
adapter: ## Run the fake source adapter as a worker on :8090
	$(PY) -m astra_adapter.serve --adapter fake --port 8090

.PHONY: test-integration
test-integration: ## Database-backed tests; needs `make dev-up` and `make migrate`
	cd $(GRAPH_SVC) && $(PY) -m pytest -m integration -q

.PHONY: bench
bench: ## Latency benchmark on a 1,000-workbook estate; needs `make dev-up` and `make migrate`
	cd $(GRAPH_SVC) && $(PY) -m pytest -m slow -q -s

.PHONY: seed
seed: ## Write a test estate through the real write path
	cd $(GRAPH_SVC) && $(PY) tools/seed_test_estate.py --workbooks 25

.PHONY: verify-replay
verify-replay: ## Rebuild the estate from its event stream and compare (S1.1.3)
	cd $(GRAPH_SVC) && $(PY) tools/verify_replay.py

.PHONY: rule-regression-check
rule-regression-check: ## Re-render every rules-engine Measure and report regressions (S5.2.2)
	cd $(GRAPH_SVC) && $(PY) tools/rule_regression_check.py

.PHONY: harvest
harvest: ## Harvest the local fixture estate through the running service
	curl -sS -X POST localhost:8080/v1/harvests 	  -H 'content-type: application/json' 	  -H 'X-Astra-Principal: user:pm@artizent.example' 	  -H 'X-Astra-Roles: programme_manager' 	  -d '{"site":"rqa","credential":"tableau/rqa"}'

.PHONY: ci
ci: check lint test conformance console-ci ## Everything CI runs except the integration suite

.PHONY: console-install
console-install: ## Install the console's dependencies
	cd $(CONSOLE_WEB) && npm ci

.PHONY: console-dev
console-dev: ## Run the console against a local graph-svc on :8080
	cd $(CONSOLE_WEB) && npm run dev

.PHONY: console-ci
console-ci: ## Type-check, lint and test the console
	cd $(CONSOLE_WEB) && npm run typecheck && npm run lint && npm run test

.PHONY: console-build
console-build: ## Build the console bundle
	cd $(CONSOLE_WEB) && npm run build
