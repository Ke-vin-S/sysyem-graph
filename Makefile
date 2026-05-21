PYTHON ?= python3
VENV   ?= .venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python
PYTEST  = $(VENV)/bin/pytest
RUFF    = $(VENV)/bin/ruff

# `docker` and `podman compose` are CLI-compatible; pick whichever you have.
COMPOSE ?= docker compose

.PHONY: help install dev neo4j-up neo4j-down test lint format typecheck clean \
        docker-build docker-test docker-shell docker-ingest docker-down \
        api-dev ui-install ui-dev ui-build product-up product-down

help:
	@echo "Targets (host venv):"
	@echo "  install        - create venv and install runtime deps"
	@echo "  dev            - install dev deps (pytest, ruff, mypy)"
	@echo "  neo4j-up       - boot local Neo4j via docker compose"
	@echo "  neo4j-down     - stop local Neo4j"
	@echo "  test           - run pytest"
	@echo "  lint           - ruff check"
	@echo "  format         - ruff format"
	@echo "  typecheck      - mypy"
	@echo "  clean          - remove caches and build artifacts"
	@echo ""
	@echo "Targets (containerized — same Linux env everywhere):"
	@echo "  docker-build   - build the dev image"
	@echo "  docker-test    - run pytest inside the dev container"
	@echo "  docker-shell   - drop into a bash shell in the dev container"
	@echo "  docker-ingest  - run sg-ingest inside the container (uses ./data)"
	@echo "  docker-down    - tear down all compose services"
	@echo ""
	@echo "Targets (product — explorer UI + API):"
	@echo "  api-dev        - start FastAPI on :8000 (host venv)"
	@echo "  ui-install     - npm install in ./ui"
	@echo "  ui-dev         - start Vite dev server on :5173 (host node)"
	@echo "  ui-build       - production build of the UI to ./ui/dist"
	@echo "  product-up     - boot neo4j + api + ui via docker compose"
	@echo "  product-down   - tear down product profile"

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

install: $(VENV)/bin/activate
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -e .

dev: install
	$(PIP) install -r requirements-dev.txt

neo4j-up:
	docker compose up -d neo4j

neo4j-down:
	docker compose down

test:
	$(PYTEST)

lint:
	$(RUFF) check .

format:
	$(RUFF) format .

typecheck:
	$(VENV)/bin/mypy core ingestion

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# ---- containerized dev ----------------------------------------------------
# Override COMPOSE to use podman: `make COMPOSE="podman compose" docker-test`

docker-build:
	$(COMPOSE) --profile dev build dev

docker-test:
	$(COMPOSE) --profile dev run --rm dev pytest

docker-shell:
	$(COMPOSE) --profile dev run --rm dev bash

docker-ingest:
	$(COMPOSE) --profile dev run --rm \
	    -e TESTPARSER_ROOT=/workspace/data dev \
	    sg-ingest --out ./out --skip datadog --skip github

docker-down:
	$(COMPOSE) --profile dev down

# ---- product stack -------------------------------------------------------

api-dev:
	$(VENV)/bin/uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

ui-install:
	cd ui && npm install --no-audit --no-fund

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

product-up:
	$(COMPOSE) --profile product up -d

product-down:
	$(COMPOSE) --profile product down
