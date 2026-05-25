# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- documentação em `docs/` para arquitetura, API, desenvolvimento e planejamento
- arquivo `.env.example` para configuração compartilhável
- `LICENSE` e estrutura inicial de `CHANGELOG`

### Changed

- `README.md` reescrito para onboarding e compartilhamento do projeto
- `docker-compose.yml` parametrizado com defaults seguros e sem `container_name` fixo
- `Makefile` simplificado para uso colaborativo
- `pyproject.toml`, `.python-version` e `requirements.txt` alinhados ao runtime real do serviço
- projeto reorganizado para layout `src/docling_service/`, mais próximo de projetos criados com `uv`

### Removed

- artefatos locais e arquivos não utilizados do workspace
- volume `data/` sem uso efetivo no fluxo principal da API
