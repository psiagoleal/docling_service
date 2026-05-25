SHELL := /bin/bash
PORT ?= 8010
FILE ?=

.PHONY: help up build down logs config health smoke

help:
	@printf '%s\n' \
		'Targets disponíveis:' \
		'  make up              # builda a imagem e sobe o serviço' \
		'  make build           # rebuild completo da imagem' \
		'  make down            # derruba o stack' \
		'  make logs            # acompanha logs do serviço' \
		'  make config          # valida a configuração do docker compose' \
		'  make health          # valida o endpoint /health do serviço' \
		'  make smoke FILE=...  # faz um POST /convert usando um arquivo local'

up:
	docker compose up -d --build

build:
	docker compose build --no-cache

down:
	docker compose down --remove-orphans

logs:
	docker compose logs -f docling

config:
	docker compose config

health:
	@echo 'Verificando http://localhost:$(PORT)/health'
	@curl -fsS "http://localhost:$(PORT)/health" && echo

smoke:
	@if [ -z "$(FILE)" ]; then \
		echo 'Uso: make smoke FILE=./seu-arquivo.pdf'; \
		exit 1; \
	fi
	curl -sS -X POST "http://localhost:$(PORT)/convert" -F "file=@$(FILE)"
