.DEFAULT_GOAL := help
.PHONY: help install dev dev-backend dev-frontend lint lint-backend lint-frontend format test test-backend test-frontend migrate migration
.PHONY: env-check up down build logs test-db-up test-db-down

BACKEND := backend
FRONTEND := frontend

help:
	@echo "make install   install backend (uv) and frontend (npm) deps from lockfiles"
	@echo "make dev       run backend (:8000) and frontend (:5173) with reload"
	@echo "make lint      ruff + eslint + tsc"
	@echo "make format    ruff format + ruff --fix"
	@echo "make test      pytest + vitest"
	@echo "make migrate   alembic upgrade head against DATABASE_URL"
	@echo "make migration name=...  autogenerate an alembic revision"
	@echo "make build     build docker images (backend, web)"
	@echo "make up        start the stack in the background (needs .env)"
	@echo "make down      stop the stack (volumes are kept)"
	@echo "make logs      follow stack logs"
	@echo "make test-db-up / test-db-down  throwaway Postgres on 127.0.0.1:55432"

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

migrate:
	cd $(BACKEND) && uv run alembic upgrade head

migration:
	@test -n "$(name)" || { echo 'usage: make migration name="add users"'; exit 1; }
	cd $(BACKEND) && uv run alembic revision --autogenerate -m "$(name)"

COMPOSE := docker compose

env-check:
	@test -f .env || { echo ".env missing: cp .env.example .env and edit it"; exit 1; }

build: env-check
	$(COMPOSE) build

up: env-check
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

test-db-up:
	$(COMPOSE) --profile test up -d --wait db-test

test-db-down:
	$(COMPOSE) --profile test rm -sf db-test
