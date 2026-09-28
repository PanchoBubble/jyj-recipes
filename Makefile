.DEFAULT_GOAL := help
.PHONY: help install dev dev-backend dev-frontend lint lint-backend lint-frontend format test test-backend test-frontend

BACKEND := backend
FRONTEND := frontend

help:
	@echo "make install   install backend (uv) and frontend (npm) deps from lockfiles"
	@echo "make dev       run backend (:8000) and frontend (:5173) with reload"
	@echo "make lint      ruff + eslint + tsc"
	@echo "make format    ruff format + ruff --fix"
	@echo "make test      pytest + vitest"

install:
	cd $(BACKEND) && uv sync --locked
	cd $(FRONTEND) && npm ci

dev:
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd $(BACKEND) && uv run uvicorn jyj.main:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd $(FRONTEND) && npm run dev

lint: lint-backend lint-frontend

lint-backend:
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .

lint-frontend:
	cd $(FRONTEND) && npm run lint

format:
	cd $(BACKEND) && uv run ruff check --fix . && uv run ruff format .

test: test-backend test-frontend

test-backend:
	cd $(BACKEND) && uv run pytest

test-frontend:
	cd $(FRONTEND) && npm test
