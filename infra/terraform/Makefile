SHELL := /bin/bash

REGION        ?= ap-southeast-1
PROFILE       ?= agent-toolkit
PLATFORM      ?= linux/amd64
TAG           ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)
INSTALL_TORCH ?= false
TF            ?= terraform
AWS           ?= aws

.PHONY: help init fmt validate plan ecr build push apply bootstrap ship logs ssh db-init destroy output

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
		--build-arg INSTALL_TORCH=$(INSTALL_TORCH) \
		-t "$$IMAGE:$(TAG)" --load .

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

destroy:
	$(TF) destroy -auto-approve -input=false

output:
	@$(TF) output
