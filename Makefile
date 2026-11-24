.PHONY: up down logs test smoke check-keys

up: ## build and start the whole stack (frontend :3000, backend :8000)
	@test -f .env || (cp .env.example .env && echo 'Created .env from .env.example - put your real LLM_API_KEYS in it')
	docker compose up --build -d
	@echo 'Frontend: http://localhost:3000   Backend: http://localhost:8000/health'

down: ## stop the stack (data volume is kept; use 'docker compose down -v' to wipe it)
	docker compose down

logs:
	docker compose logs -f

test: ## run backend tests locally
	cd backend && pip install -r requirements-dev.txt && python -m pytest -q

smoke: ## contract check against a running backend: make smoke API=https://backend ORIGIN=https://frontend
	python3 scripts/smoke_test.py --api $(or $(API),http://localhost:8000) --origin $(or $(ORIGIN),http://localhost:3000)

check-keys: ## verify Gemini keys + LLM_MODEL from .env
	python3 scripts/check_keys.py
