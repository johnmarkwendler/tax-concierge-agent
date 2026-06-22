HOST ?= 127.0.0.1
PORT ?= 8080
PLAYGROUND_PORT ?= 8081

.PHONY: install service playground generate-traces grade grade-local eval eval-local

install:
	agents-cli install

service:
	uv run uvicorn tax_concierge_agent.service:app --host $(HOST) --port $(PORT) --reload

playground:
	agents-cli playground --host $(HOST) --port $(PLAYGROUND_PORT)

generate-traces:
	uv run python tests/eval/generate_traces.py

grade:
	agents-cli eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml

eval: generate-traces grade

grade-local:
	uv run python tests/eval/grade_traces.py

eval-local: generate-traces grade-local
