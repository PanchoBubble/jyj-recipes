.DEFAULT_GOAL := help
.PHONY: help install dev dev-backend dev-frontend lint lint-backend lint-frontend format test test-backend test-frontend migrate migration
.PHONY: env-check up down build logs test-db-up test-db-down test-db-prune ca-export mdns-install whisper-download backup-now restore backup-drill

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
	@echo "make ca-export copy Caddy's LAN root CA to ./caddy-root.crt (stack must be up)"
	@echo "make test-db-up / test-db-down  throwaway Postgres on 127.0.0.1:55432"
	@echo "make test-db-prune [hours=3]  drop leftover per-run jyj_test_* databases"
	@echo "make whisper-download [model=tiny|base|small|turbo]  fetch + verify a whisper model into the whisper-models volume"
	@echo "make backup-now [label=pre-upgrade]  pg_dump + photo snapshot into BACKUP_DIR now"
	@echo "make restore db=FILE photos=DIR force=yes  restore a backup (destroys current data, prompts)"
	@echo "make backup-drill  backup/destroy/restore/verify in a throwaway jyj-drill project"

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

hours ?= 3
test-db-prune:
	cd $(BACKEND) && uv run python tests/testdb.py prune --older-than-hours $(hours)

mdns-install:
	sudo install -m 0755 deploy/mdns/jyj-mdns-publish /usr/local/bin/jyj-mdns-publish
	sudo install -m 0644 deploy/mdns/jyj-mdns.service /etc/systemd/system/jyj-mdns.service
	sudo systemctl daemon-reload
	sudo systemctl enable --now jyj-mdns.service

ca-export:
	$(COMPOSE) cp web:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
	@openssl x509 -in caddy-root.crt -noout -subject -enddate -fingerprint -sha256 2>/dev/null || true

model ?= small
whisper-download:
	$(COMPOSE) run --rm --no-deps backend python -m jyj.stt.download $(model)

backup-now: env-check
	$(COMPOSE) --profile backup run --rm backup once $(label)

restore: env-check
	deploy/backup/restore.sh $(if $(db),--db "$(db)") $(if $(photos),--photos "$(photos)") $(if $(filter yes,$(force)),--yes-destroy-current-data)

backup-drill:
	deploy/backup/drill.sh
