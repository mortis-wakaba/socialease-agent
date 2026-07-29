.PHONY: dev-backend dev-calendar-mcp dev-frontend test-backend test-calendar-mcp test-postgres-runtime test-redis-context eval eval-gate eval-memory-vector eval-memory-ablation eval-llm eval-output-guardrail prompt-version-check update-prompt-versions privacy-check lock-python typecheck-frontend lint-frontend build-frontend test-e2e test-e2e-production-auth e2e-smoke migration-check ready backup-db restore-drill monitor-alerts smoke-check prod-config-check docker-up docker-down docker-reset docker-prod-config check

dev-backend:
	cd backend && uvicorn app.main:app --reload

dev-calendar-mcp:
	cd backend && python -m app.calendar.mcp_server

dev-frontend:
	cd frontend && npm run dev

test-backend:
	test -n "$(SOCIALEASE_TEST_DATABASE_URL)" || (echo "Set SOCIALEASE_TEST_DATABASE_URL to an isolated disposable PostgreSQL database" && exit 1)
	cd backend && SOCIALEASE_DATABASE_URL="$(SOCIALEASE_TEST_DATABASE_URL)" SOCIALEASE_TEST_DATABASE_URL="$(SOCIALEASE_TEST_DATABASE_URL)" pytest

test-calendar-mcp:
	cd backend && pytest -p no:rerunfailures -q tests/test_calendar_provider.py tests/test_calendar_skill.py tests/test_calendar_mcp_contract.py tests/test_calendar_api.py

test-postgres-runtime:
	test -n "$(SOCIALEASE_TEST_DATABASE_URL)" || (echo "Set SOCIALEASE_TEST_DATABASE_URL to an isolated disposable PostgreSQL database" && exit 1)
	cd backend && SOCIALEASE_TEST_DATABASE_URL="$(SOCIALEASE_TEST_DATABASE_URL)" pytest -q tests/test_postgres_*.py tests/test_heavier_load.py::test_fresh_postgres_database_can_upgrade_to_head_when_configured

test-redis-context:
	cd backend && SOCIALEASE_TEST_REDIS_URL=$${SOCIALEASE_TEST_REDIS_URL:-redis://localhost:6379/0} pytest -p no:rerunfailures -m redis_integration -s tests/test_conversation_context_cache.py tests/test_module_overlay_store.py tests/test_task_sessions.py

eval:
	cd backend && python -m app.evals.run

eval-gate:
	cd backend && python -m app.evals.gate

eval-memory-vector:
	cd backend && python -m app.evals.vector_memory_retrieval

eval-memory-ablation:
	@cd backend && python -m app.evals.memory_retrieval_ablation

eval-llm:
	cd backend && RUN_LLM_EVALS=true pytest -m llm_eval tests/test_deepeval_quality.py tests/test_output_guardrail_quality.py

eval-output-guardrail:
	cd backend && RUN_LLM_EVALS=true pytest -m llm_eval -s tests/test_output_guardrail_quality.py

prompt-version-check:
	cd backend && python -m app.llm.prompt_version_check

update-prompt-versions:
	cd backend && python -m app.llm.prompt_version_check --update

privacy-check:
	python scripts/check_repository_privacy.py

lock-python:
	cd backend && PIP_TOOLS_CACHE_DIR=/tmp/socialease-pip-tools-cache pip-compile --generate-hashes --output-file=requirements-runtime.lock requirements-runtime.txt
	cd backend && PIP_TOOLS_CACHE_DIR=/tmp/socialease-pip-tools-cache pip-compile --generate-hashes --output-file=requirements.lock requirements.txt

typecheck-frontend:
	cd frontend && npm run typecheck

lint-frontend:
	cd frontend && npm run lint

build-frontend:
	cd frontend && npm run build

test-e2e:
	cd frontend && npm run test:e2e

test-e2e-production-auth:
	cd frontend && npm run test:e2e:production-auth

e2e-smoke:
	bash scripts/e2e_smoke.sh

migration-check:
	cd backend && python -m app.db.migration_check --check-names-only

ready:
	curl -fsS http://127.0.0.1:8000/ready

backup-db:
	bash scripts/backup_database.sh

restore-drill:
	test -n "$(BACKUP_FILE)" || (echo "Usage: make restore-drill BACKUP_FILE=<backup-file>" && exit 1)
	bash scripts/restore_drill.sh "$(BACKUP_FILE)"

monitor-alerts:
	python scripts/monitor_alerts.py --dry-run

smoke-check:
	python scripts/deployment_smoke_check.py

prod-config-check:
	docker compose -f docker-compose.prod.yml --env-file .env.production config >/tmp/socialease-prod-compose.yml

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-reset:
	docker compose down -v

docker-prod-config:
	docker compose -f docker-compose.prod.yml --env-file .env.production config

check: test-backend eval eval-gate typecheck-frontend lint-frontend build-frontend test-e2e test-e2e-production-auth migration-check
