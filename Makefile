# The human interface to the platform. `make up` needs only docker + make.

COMPOSE := docker compose
SERVICE ?=
N ?= 2

.PHONY: up down reset logs build deploy rollback scale load load-ramp load-stop demo-kill-worker demo-kill-api

# Foreground by default: log streams from every service in this terminal
# (Ctrl-C stops the stack). `make up D=1` detaches instead.
up:
	@echo ""
	@echo "  gateway     http://localhost:8080          (POST /interviews, GET /score/{id}, /stats)"
	@echo "  grafana     http://localhost:3000          (Interviewd Ops dashboard, no login)"
	@echo "  prometheus  http://localhost:9090"
	@echo ""
	$(COMPOSE) up --build $(if $(D),-d,)

down:
	$(COMPOSE) down --remove-orphans

reset:
	$(COMPOSE) down -v --remove-orphans

logs:
	@echo ">> tip: Grafana (localhost:3000) has searchable logs for all services"
	$(COMPOSE) logs -f --tail=100

# Build all app images (or one: make build SERVICE=api).
build:
	npx nx run-many -t build $(if $(SERVICE),-p $(SERVICE),)

# Build a versioned image, repoint its tag in .env, recreate only that service.
deploy:
	@test -n "$(SERVICE)" || (echo "usage: make deploy SERVICE=api|worker|gateway|autoscaler" && exit 1)
	@TAG=$$(git rev-parse --short HEAD 2>/dev/null || date +%s); \
	VAR=$$(echo $(SERVICE) | tr a-z A-Z)_TAG; \
	echo ">> building interviewd-$(SERVICE):$$TAG"; \
	TAG=$$TAG npx nx run $(SERVICE):build; \
	grep -v "^$$VAR=" .env > .env.tmp && echo "$$VAR=$$TAG" >> .env.tmp && mv .env.tmp .env; \
	echo ">> repointed $$VAR=$$TAG"; \
	$(COMPOSE) up -d $(SERVICE)

# Repoint to an existing image tag (docker images interviewd-<service> lists them).
rollback:
	@test -n "$(SERVICE)" -a -n "$(TAG)" || (echo "usage: make rollback SERVICE=api TAG=abc123" && exit 1)
	@VAR=$$(echo $(SERVICE) | tr a-z A-Z)_TAG; \
	grep -v "^$$VAR=" .env > .env.tmp && echo "$$VAR=$(TAG)" >> .env.tmp && mv .env.tmp .env; \
	echo ">> repointed $$VAR=$(TAG)"; \
	$(COMPOSE) up -d $(SERVICE)

# Manual scale override (the autoscaler will fight you — that's a feature to demo).
scale:
	@test -n "$(SERVICE)" || (echo "usage: make scale SERVICE=worker N=4" && exit 1)
	$(COMPOSE) up -d --scale $(SERVICE)=$(N) --no-recreate $(SERVICE)

# Load tests (artillery via npx — no global install).
load:
	npx artillery run loadtest/steady.yml

load-ramp:
	npx artillery run loadtest/ramp.yml

# Failure drills — the autoscaler stays LIVE and self-heals both. Run with the
# dial at ~25 rps and Grafana open. Two flavors:
#   demo-kill-worker: fleet dies -> depth + oldest-age climb -> autoscaler
#     resurrects workers from the stopped template -> backlog drains, no loss.
#   demo-kill-api: api fleet dies -> gateway 502s, error rates spike on the
#     status panel -> autoscaler restores min api replicas -> traffic recovers.
demo-kill-worker:
	@echo ">> killing the entire worker fleet. watch: depth climbs, replicas hit 0, then self-heal."
	$(COMPOSE) stop worker
	@docker ps -a --filter label=com.docker.compose.service=worker --filter status=exited -q | grep -v $$(docker ps -aqf name=interviewd-worker-1) | xargs docker rm 2>/dev/null || true
	@echo ">> fleet down. autoscaler resurrects within ~15s; when depth drains, run: docker compose start worker"

demo-kill-api:
	@echo ">> killing the api fleet. watch: 5xx on the status panel, dial errors climb, then self-heal."
	$(COMPOSE) stop api
	@docker ps -a --filter label=com.docker.compose.service=api --filter status=exited -q | grep -v $$(docker ps -aqf name=interviewd-api-1) | xargs docker rm 2>/dev/null || true
	@echo ">> api down. autoscaler restores min replicas within ~15s; then run: docker compose start api"
