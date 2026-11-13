# ORCAS development, evaluation, and full-stack targets.
.PHONY: ingest eval eval-live eval-baseline experiment test demo up down doctor loadtest

up:
	docker compose up --build -d

down:
	docker compose down

doctor:
	python scripts/doctor.py

loadtest:
	locust -f loadtest/locustfile.py --headless --host http://localhost:8000 --users 10 --spawn-rate 2 --run-time 15m --stop-timeout 180

ingest:
	python -m services.ingest

# Agent with in-process stub replies (no services needed; reflects the stub, not model quality)
eval:
	python -m eval.run_eval

# Agent with the live model behind LLM_GATEWAY_URL; paced for free-tier rate limits
eval-live:
	python -m eval.run_eval --llm gateway --sleep 2

# Pre-registered agent experiment on the live model, e.g. make experiment NAME=l2-reflect-prompt
experiment:
	python -m eval.experiment run $(NAME) --llm gateway --sleep 2

# Keyword baseline, kept as a reference point only
eval-baseline:
	python -m eval.run_eval --sut baseline

test:
	python -m pytest tests -q

demo:
	python scripts/demo/initial_demo.py

# Everything the test suite needs (all services + eval + test tools); tests that need the
# built index skip until `make ingest` has run
.PHONY: install-dev
install-dev:
	python -m pip install -r requirements-dev.txt
