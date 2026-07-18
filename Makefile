.PHONY: dev-backend dev-frontend test-backend test-redis-context eval eval-gate eval-llm eval-output-guardrail typecheck-frontend lint-frontend build-frontend test-e2e test-e2e-production-auth e2e-smoke migration-check ready backup-db restore-drill monitor-alerts smoke-check prod-config-check docker-up docker-down docker-reset docker-prod-config check

dev-backend:
	cd backend && uvicorn app.main:app --reload

dev-frontend:
	cd frontend && npm run dev

test-backend:
	cd backend && pytest

test-redis-context:
	cd backend && SOCIALEASE_TEST_REDIS_URL=$${SOCIALEASE_TEST_REDIS_URL:-redis://localhost:6379/0} pytest -p no:rerunfailures -m redis_integration -s tests/test_roleplay_session_context.py tests/test_task_sessions.py

eval:
	cd backend && python -m app.evals.run

eval-gate:
	cd backend && python -m app.evals.gate

eval-llm:
	cd backend && RUN_LLM_EVALS=true pytest -m llm_eval tests/test_deepeval_quality.py tests/test_output_guardrail_quality.py

eval-output-guardrail:
	cd backend && RUN_LLM_EVALS=true pytest -m llm_eval -s tests/test_output_guardrail_quality.py

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
