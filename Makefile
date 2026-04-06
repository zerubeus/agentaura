.PHONY: up down logs status restart reset test lint typecheck check

# --- Docker Compose ---

up: .env ## Start the full stack
	docker compose up -d

down: ## Stop all services
	docker compose down

logs: ## Tail all service logs
	docker compose logs -f

status: ## Show service status
	docker compose ps

restart: ## Restart all services
	docker compose restart

reset: ## Stop and remove all data (destructive!)
	docker compose down -v

.env: .env.example ## Create .env from example if missing
	cp .env.example .env
	@echo "Created .env from .env.example — edit secrets before running 'make up'"

# --- Development ---

test: ## Run tests
	uv run pytest tests/ -v

lint: ## Run ruff linter
	uv run ruff check agentaura/ tests/

typecheck: ## Run pyright type checker
	uv run pyright agentaura/

check: lint typecheck test ## Run all checks (lint + typecheck + test)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
