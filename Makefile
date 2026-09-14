.PHONY: install seed run test eval index verify docker-up docker-down

install:
	python -m pip install -r requirements-dev.txt

seed:
	python -m scripts.seed_data

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

eval:
	python evaluator/run_eval.py

index:
	python -m scripts.index_rules

verify:
	python -m scripts.verify_project

docker-up:
	docker compose up --build -d --wait

docker-down:
	docker compose down
