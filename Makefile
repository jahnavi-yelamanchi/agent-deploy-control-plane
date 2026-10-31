SHELL := /bin/bash
export MODEL_PROVIDER ?= mock
UV ?= uv
PY := $(UV) run python

.PHONY: help install bootstrap lint typecheck test eval eval-gate promptfoo report demo-local demo up down \
        validate-k8s plan-aws plan-azure slo-rules chaos finops infracost fixtures freeze

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "%-14s %s\n", $$1, $$2}'

install: ## uv sync with dev + evals extras
	$(UV) sync --extra dev --extra evals

bootstrap: ## flip from mock to real providers (reads .env)
	./scripts/bootstrap.sh

lint: ## ruff
	$(UV) run ruff check src tests scripts evals
	$(UV) run ruff format --check src tests scripts evals

typecheck: ## mypy
	$(UV) run mypy

test: ## smoke + e2e tests
	$(UV) run pytest

fixtures: ## re-record provider fixtures (mock synthesizes; real providers record)
	$(PY) scripts/record_fixtures.py

freeze: ## snapshot eval tasks to evals/frozen/v1 with hash
	$(PY) -m adcp.evals.freeze

eval: ## offline eval of a spec on the frozen set
	$(PY) -m adcp.evals.runner --spec specs/v1.yaml

eval-gate: ## Inspect AI suite + threshold gate
	$(PY) -m adcp.evals.gate --spec specs/v1.yaml

promptfoo: ## prompt-level regression
	cd evals/promptfoo && npx --yes promptfoo@0.103.5 eval -c promptfooconfig.yaml --no-cache --no-write

report: ## runs/*.jsonl -> results/results.md + results/cost_vs_quality.png
	$(PY) scripts/report.py

demo-local: ## full deploy->eval->canary->rollback loop in-process with mocks
	$(PY) -m adcp.demo

demo: ## same loop against the kind cluster from `make up`
	./scripts/demo.sh

up: ## kind + Argo CD + Rollouts + Istio + observability + data plane + runtime
	./scripts/up.sh

down: ## tear down kind cluster and compose stack
	./scripts/down.sh

validate-k8s: ## helm lint + kubeconform + kustomize build
	./scripts/validate_k8s.sh

plan-aws: ## terraform init/validate/test(plan with mock providers) for EKS
	cd terraform/aws && terraform init -backend=false -input=false && terraform validate && terraform test

plan-azure: ## terraform init/validate/test(plan with mock providers) for AKS
	cd terraform/azure && terraform init -backend=false -input=false && terraform validate && terraform test

slo-rules: ## generate Prometheus rules from slo/slo.yaml
	$(PY) scripts/gen_slo_rules.py

chaos: ## chaos suite: MTTD / MTTR
	$(PY) scripts/chaos_suite.py

finops: ## per-deploy, per-1k-task and AKS-vs-EKS cost tables
	$(PY) -m adcp.finops.report

infracost: ## Infracost breakdown if installed, static table otherwise
	./scripts/infracost.sh
