SHELL := /bin/bash

TF            ?= terraform
AWS           ?= aws
REGION        ?= $(shell $(TF) output -raw aws_region 2>/dev/null || echo ap-southeast-2)
PROFILE       ?= agent-toolkit
PLATFORM      ?= linux/amd64
TAG           ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)
INSTALL_TORCH ?= false
ROOT          ?= $(shell git rev-parse --show-toplevel)

.PHONY: help init fmt validate plan ecr build push apply bootstrap ship logs ssh db-init stop start destroy output

help:
	@echo "Find My Mines - Terraform / ECR targets"
	@echo
	@echo "  make init       Download providers"
	@echo "  make validate   terraform fmt check + validate"
	@echo "  make plan       Show the pending changes"
	@echo "  make ecr        Create the ECR repository only"
	@echo "  make push       Build linux/amd64 image and push :<git-sha>"
	@echo "  make apply      Create/update EC2, RDS, and the image parameter"
	@echo "  make bootstrap  Re-run the host bootstrap (pull + init + restart)"
	@echo "  make ship       ecr + push + apply + bootstrap"
	@echo "  make logs       Tail CloudWatch-backed container logs over SSH"
	@echo "  make ssh        Open a shell on the host"
	@echo "  make db-init    Re-run schema/runtime-role bootstrap only"
	@echo "  make stop       Stop EC2 and RDS to save cost while idle"
	@echo "  make start      Start RDS and EC2, then re-run the bootstrap"
	@echo "  make destroy    Tear everything down"
	@echo

init:
	$(TF) init -input=false

fmt:
	$(TF) fmt -recursive

validate: init
	$(TF) fmt -check -recursive
	$(TF) validate

plan: init
	$(TF) plan -input=false

ecr: init
	$(TF) apply -auto-approve -input=false -target=aws_ecr_repository.app

build:
	@IMAGE="$$($(TF) output -raw ecr_repository_url)"; \
	docker buildx build --platform $(PLATFORM) \
		-f "$(ROOT)/Dockerfile" \
		--build-arg INSTALL_TORCH=$(INSTALL_TORCH) \
		-t "$$IMAGE:$(TAG)" --load "$(ROOT)"

push: build
	@IMAGE="$$($(TF) output -raw ecr_repository_url)"; \
	$(AWS) ecr get-login-password --region $(REGION) --profile $(PROFILE) \
		| docker login --username AWS --password-stdin "$${IMAGE%%/*}"; \
	docker push "$$IMAGE:$(TAG)"

apply: init
	$(TF) apply -auto-approve -input=false -var "image_tag=$(TAG)"

bootstrap:
	@HOST="$$($(TF) output -raw server_host)"; \
	KEY="$$($(TF) output -raw ssh_key_path)"; \
	ssh -i "$$KEY" ec2-user@"$$HOST" 'sudo /usr/local/bin/find-my-mines-bootstrap'

ship: ecr push apply bootstrap

logs:
	@HOST="$$($(TF) output -raw server_host)"; \
	KEY="$$($(TF) output -raw ssh_key_path)"; \
	ssh -i "$$KEY" ec2-user@"$$HOST" \
		'cd /opt/find-my-mines && sudo docker compose --env-file .env -f compose.aws.yaml logs -f --tail=200 game'

ssh:
	@HOST="$$($(TF) output -raw server_host)"; \
	KEY="$$($(TF) output -raw ssh_key_path)"; \
	ssh -i "$$KEY" ec2-user@"$$HOST"

db-init:
	@HOST="$$($(TF) output -raw server_host)"; \
	KEY="$$($(TF) output -raw ssh_key_path)"; \
	ssh -i "$$KEY" ec2-user@"$$HOST" \
		'cd /opt/find-my-mines && sudo docker compose --env-file .env -f compose.aws.yaml --profile init run --rm init-db'

# Idle mode: keep the data, stop the compute. The Elastic IP, EBS volume and
# RDS storage still bill (roughly USD 8/month); RDS auto-starts after 7 days.
stop: init
	@ID="$$($(TF) output -raw instance_id)"; \
	DB="$$($(TF) output -raw db_instance_identifier)"; \
	echo "Stopping EC2 $$ID and RDS $$DB..."; \
	$(AWS) ec2 stop-instances --profile $(PROFILE) --region $(REGION) \
		--instance-ids "$$ID" >/dev/null; \
	$(AWS) rds stop-db-instance --profile $(PROFILE) --region $(REGION) \
		--db-instance-identifier "$$DB" >/dev/null || \
		echo "RDS was not stopped (already stopped or busy)."; \
	echo "Idle. Run 'make start' before the demo."

start: init
	@ID="$$($(TF) output -raw instance_id)"; \
	DB="$$($(TF) output -raw db_instance_identifier)"; \
	echo "Starting RDS $$DB (this takes a few minutes)..."; \
	$(AWS) rds start-db-instance --profile $(PROFILE) --region $(REGION) \
		--db-instance-identifier "$$DB" >/dev/null 2>&1 || true; \
	$(AWS) rds wait db-instance-available --profile $(PROFILE) \
		--region $(REGION) --db-instance-identifier "$$DB"; \
	echo "Starting EC2 $$ID..."; \
	$(AWS) ec2 start-instances --profile $(PROFILE) --region $(REGION) \
		--instance-ids "$$ID" >/dev/null; \
	$(AWS) ec2 wait instance-running --profile $(PROFILE) --region $(REGION) \
		--instance-ids "$$ID"; \
	$(AWS) ec2 wait instance-status-ok --profile $(PROFILE) --region $(REGION) \
		--instance-ids "$$ID"; \
	$(MAKE) bootstrap

destroy:
	$(TF) destroy -auto-approve -input=false

output:
	@$(TF) output
