.DEFAULT_GOAL := help
SHELL := /bin/bash
DATE = $(shell date +%Y-%m-%d:%H:%M:%S)

APP_VERSION_FILE = app/version.py

GIT_BRANCH ?= $(shell git symbolic-ref --short HEAD 2> /dev/null || echo "detached")
GIT_COMMIT ?= $(shell git rev-parse HEAD)

.PHONY: help
help:
	@cat $(MAKEFILE_LIST) | grep -E '^[a-zA-Z_-]+:.*?## .*$$' | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

.PHONY: generate-version-file
generate-version-file: ## Generates the app version file
	@printf "__commit_sha__ = \"${GIT_COMMIT}\"\n__time__ = \"${DATE}\"\n" > ${APP_VERSION_FILE}

.PHONY: test
test: generate-version-file ## Run tests
	./scripts/run_tests.sh

.PHONY: freeze-requirements
freeze-requirements:
	poetry lock --no-update

.PHONY: test-requirements
	poetry lock --check

.PHONY: coverage
coverage: venv ## Create coverage report
	. venv/bin/activate && coveralls

.PHONY: clean
clean:
	rm -rf node_modules cache target venv .coverage build tests/.cache

.PHONY: format
format:
	poetry run isort .
	poetry run black --config pyproject.toml .
	poetry run flake8 .
	poetry run mypy .

.PHONY: smoke-test
smoke-test:
	cd tests_smoke && poetry run python smoke_test.py

.PHONY: run
run:
	poetry run flask run -p 6011 --host=0.0.0.0
