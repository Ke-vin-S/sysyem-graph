PYTHON ?= python3
VENV   ?= .venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python
PYTEST  = $(VENV)/bin/pytest
RUFF    = $(VENV)/bin/ruff

.PHONY: help install dev neo4j-up neo4j-down test lint format typecheck clean

help:
	@echo "Targets:"
	@echo "  install     - create venv and install runtime deps"
	@echo "  dev         - install dev deps (pytest, ruff, mypy)"
	@echo "  neo4j-up    - boot local Neo4j via docker-compose"
	@echo "  neo4j-down  - stop local Neo4j"
	@echo "  test        - run pytest"
	@echo "  lint        - ruff check"
	@echo "  format      - ruff format"
	@echo "  typecheck   - mypy"
	@echo "  clean       - remove caches and build artifacts"

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
