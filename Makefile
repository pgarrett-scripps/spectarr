.PHONY: dev up down test backend-test frontend-test services-install services-test typecheck prepare release-check release-rehearsal version-check backup verify-backup restore-test

BACKEND_PYTEST := $(if $(wildcard backend/.venv/bin/pytest),cd backend && .venv/bin/pytest,cd backend && uv run --extra dev pytest)
SERVICE_PYTHON := services/.venv/bin/python
MSCONVERT_CLI_SOURCE ?= ../msconvert-cli
MZMLPY_SOURCE ?= ../mzmlpy
SPXTACULAR_SOURCE ?= ../spxtacular
RELEASE_ENV ?= release/.env
TEST_JOBS ?= 3

prepare:
	mkdir -p data/storage data/scratch imports

dev: prepare
	docker compose up --build

up: prepare
	docker compose up -d --build

down:
	docker compose down

test: version-check
	+$(MAKE) --output-sync=target -j$(TEST_JOBS) backend-test frontend-test services-test

version-check:
	python3 scripts/check_version.py

backend-test:
	$(BACKEND_PYTEST)

frontend-test:
	cd frontend && npm test -- --run && npm run typecheck && npm run lint && npm run build

services-install:
	uv venv --allow-existing --python 3.12 services/.venv
	test -f "$(MSCONVERT_CLI_SOURCE)/pyproject.toml"
	test -f "$(MZMLPY_SOURCE)/pyproject.toml"
	test -f "$(SPXTACULAR_SOURCE)/pyproject.toml"
	uv pip install --python $(SERVICE_PYTHON) "$(MSCONVERT_CLI_SOURCE)" "$(MZMLPY_SOURCE)" "$(SPXTACULAR_SOURCE)" services/agent services/converter services/extractor services/mcp services/webhooks

services-test: services-install
	PYTHONPATH=services/agent/src $(SERVICE_PYTHON) -m unittest discover -s services/agent/tests
	PYTHONPATH=services/converter/src $(SERVICE_PYTHON) -m unittest discover -s services/converter/tests
	PYTHONPATH=services/extractor/src $(SERVICE_PYTHON) -m unittest discover -s services/extractor/tests
	PYTHONPATH=services/mcp/src $(SERVICE_PYTHON) -m unittest discover -s services/mcp/tests
	PYTHONPATH=services/webhooks/src $(SERVICE_PYTHON) -m unittest discover -s services/webhooks/tests

typecheck:
	cd frontend && npm run typecheck

release-check: version-check
	@test -f "$(RELEASE_ENV)"
	docker compose --env-file "$(RELEASE_ENV)" -f release/compose.yaml config --quiet

release-rehearsal: version-check
	scripts/release-rehearsal.sh

backup:
	test -n "$(BACKUP_DIR)"
	scripts/backup.sh "$(BACKUP_DIR)"

verify-backup:
	test -n "$(BACKUP_DIR)"
	scripts/verify-backup.sh "$(BACKUP_DIR)"

restore-test:
	test -n "$(BACKUP_DIR)"
	test -n "$(RESTORE_DIR)"
	scripts/restore-test.sh "$(BACKUP_DIR)" "$(RESTORE_DIR)"
