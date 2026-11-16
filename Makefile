.PHONY: setup env-check web chat check test docker-build docker-up docker-down k8s-apply k8s-port-forward

ENV_FILE ?= .env

setup:
	conda run -n ai python -m pip install -r requirements.txt

env-check:
	python3 -m src.deployment_check --env-file $(ENV_FILE)

web:
	conda run -n ai python main.py --web

chat:
	conda run -n ai python main.py --chat

check:
	conda run -n ai python main.py --check

test:
	conda run -n ai python -W error::ResourceWarning -m unittest discover -s tests -v

docker-build:
	docker compose build

docker-up: env-check
	docker compose up -d --build

docker-down:
	docker compose down

k8s-apply:
	kubectl apply -f deploy/kubernetes/ops-ai.yaml

k8s-port-forward:
	kubectl port-forward svc/ops-ai 8765:8765
