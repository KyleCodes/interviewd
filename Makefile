# The human interface to the platform. `make up` needs only docker + make.

COMPOSE := docker compose
SERVICE ?=
N ?= 2

.PHONY: up down reset logs build deploy rollback scale load load-ramp load-stop demo-failure

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

# Guided worker-outage drill under load — watch depth + oldest-age climb, then drain.
# The autoscaler must be suspended first or it resurrects the fleet within one
# poll (that self-healing is its own demo — see runbook "fleet-kill").
demo-failure:
	@echo ">> suspending autoscaler (else it self-heals), stopping all workers."
	@echo ">> start load in another terminal: make load (or set the dial ~25)"
	$(COMPOSE) stop autoscaler worker
	@printf ">> workers down, jobs buffering durably in postgres. watch depth + oldest-age climb. press enter to recover... "; read _
	$(COMPOSE) start worker autoscaler
	@echo ">> worker + autoscaler back — watch the fleet scale out and depth drain to 0."
