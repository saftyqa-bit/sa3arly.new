.PHONY: test lint seed-verify capacity-verify catalog-discovery-verify launch-readiness compile verify local-up bootstrap bootstrap-firestore refresh hourly zip autonomous-bootstrap web-deploy

test:
	pytest

lint:
	ruff check app scripts tests

seed-verify:
	python scripts/verify_seed.py

capacity-verify:
	python scripts/verify_hourly_capacity.py

catalog-discovery-verify:
	python scripts/verify_catalog_discovery_readiness.py

launch-readiness:
	python scripts/verify_launch_readiness.py

compile:
	python -m compileall -q app scripts tests

verify: lint seed-verify capacity-verify catalog-discovery-verify test compile

local-up:
	docker compose up -d postgres
	docker compose run --rm bootstrap
	docker compose up --build api worker

bootstrap:
	python scripts/bootstrap_db.py

bootstrap-firestore:
	PERSISTENCE_BACKEND=firestore python scripts/bootstrap_firestore.py

refresh:
	python scripts/run_hourly_local.py

hourly: refresh

zip:
	rm -f ../Sa3arly-v0.6.1-GitHub-Autonomous.zip
	zip -r ../Sa3arly-v0.6.1-GitHub-Autonomous.zip . -x '.git/*' '.sites-runtime/*' 'node_modules/*' 

.PHONY: migration-contract github-wif-bootstrap
migration-contract:
	pytest -q tests/test_migration_contract.py

github-wif-bootstrap:
	bash scripts/bootstrap_github_wif.sh

autonomous-bootstrap:
	bash scripts/bootstrap_autonomous_delivery.sh

web-deploy:
	bash scripts/deploy_web_cloud_run.sh
