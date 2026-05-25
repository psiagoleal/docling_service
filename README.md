# Docling Conversion Service

![Build status](https://img.shields.io/badge/build-not_configured-lightgrey)
![Coverage](https://img.shields.io/badge/coverage-not_configured-lightgrey)
![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Serviço HTTP em `FastAPI` para converter documentos `PDF` e `DOCX` em `Markdown` usando [Docling](https://github.com/DS4SD/docling), com OCR, extração de imagens e enhancement opcional com `Ollama`, `OpenAI` ou `Anthropic`.

O repositório foi reorganizado para compartilhamento com colegas, com foco em:

- configuração reproduzível via `.env.example`
- remoção de artefatos locais e arquivos desnecessários
- redução de risco de vazamento de segredos
- documentação mínima para uso, manutenção e evolução

## Principais funcionalidades

- conversão de `PDF` e `DOCX` para `Markdown`
- interface web simples em `/`
- endpoint de saúde em `/health`
- documentação interativa em `/docs`
- processamento em lote via `/convert/batch`
- OCR com `EasyOCR` (`pt` e `en`)
- extração de imagens e tabelas
- fallback `DOCX -> PDF` com `LibreOffice`
- enhancement opcional por LLM para limpeza de artefatos, equações e cabeçalhos/rodapés

## Documentação

- [Plano de saneamento](docs/planning.md)
- [Arquitetura](docs/architecture.md)
- [API](docs/api.md)
- [Guia de desenvolvimento](docs/development.md)
- [Histórico de mudanças](CHANGELOG.md)

## Pré-requisitos

### Recomendado para execução principal

- `Docker` e `Docker Compose`
- GPU NVIDIA compatível com CUDA 12.1+
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

### Opcional

- `uv` para setup local de desenvolvimento
- `Ollama` local, se quiser enhancement sem provedor cloud
- chaves da `OpenAI` e/ou `Anthropic`, se quiser usar provedores cloud

## Instalação

### Opção A — Docker (recomendada)

```bash
cp .env.example .env
```

Edite o arquivo `.env` apenas se quiser:

- trocar a porta publicada
- configurar `Ollama`
- habilitar `OpenAI` / `Anthropic`
- ajustar parâmetros do enhancement por LLM

Suba o serviço:

```bash
docker compose up -d --build
```

Se o seu ambiente precisar de rede do host durante o build Docker, mantenha esse ajuste em um arquivo local `docker-compose.override.yml` não versionado:

```yaml
services:
  docling:
    build:
      network: host
```

A aplicação ficará disponível em:

- interface web: `http://localhost:8010/`
- healthcheck: `http://localhost:8010/health`
- Swagger UI: `http://localhost:8010/docs`

> Se alterar `DOCLING_HOST_PORT` no `.env`, use a nova porta no lugar de `8010`.

### Opção B — Desenvolvimento local com `uv`

> Esta opção é útil para desenvolvimento e depuração. Para uso com GPU CUDA, o fluxo Docker continua sendo o mais previsível.

```bash
uv python install 3.12
uv sync
uv run uvicorn --app-dir src docling_service.app:app --reload --host 0.0.0.0 --port 8001
```

## Configuração segura

1. Copie `.env.example` para `.env`
2. Preencha apenas as variáveis realmente necessárias
3. Nunca versione `.env`, tokens, logs ou saídas geradas
4. Se alguma chave tiver sido exposta anteriormente, faça a rotação antes de publicar o repositório

Arquivos e padrões ignorados pelo Git incluem:

- `.env`
- `*.log`
- `docling_output_*/`
- caches Python e ambientes virtuais

## Exemplos de uso

### Converter um arquivo

```bash
curl -X POST "http://localhost:8010/convert" \
  -F "file=@documento.pdf"
```

### Converter e receber ZIP

```bash
curl -X POST "http://localhost:8010/convert?format=zip" \
  -F "file=@documento.docx" \
  --output resultado.zip
```

### Converter com enhancement por LLM

```bash
curl -X POST "http://localhost:8010/convert?enhance=true&text_provider=ollama" \
  -F "file=@documento.pdf"
```

### Verificar saúde do serviço via `Makefile`

```bash
make health
```

### Smoke test via `Makefile`

```bash
make smoke FILE=./documento.pdf
```

## Variáveis de ambiente principais

| Variável | Padrão | Descrição |
|---|---|---|
| `DOCLING_HOST_PORT` | `8010` | Porta publicada no host |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Endpoint do Ollama |
| `OPENAI_API_KEY` | vazio | Chave genérica da OpenAI |
| `ANTHROPIC_API_KEY` | vazio | Chave genérica da Anthropic |
| `LLM_TIMEOUT` | `600` | Timeout por requisição LLM |
| `VISION_BATCH_SIZE` | `3` | Tamanho do lote para análise visual |
| `LLM_MAX_TOKENS` | `16384` | Limite de tokens de saída |

## Estrutura de diretórios

```text
docling_service/
├── .dockerignore
├── .env.example
├── .gitignore
├── .python-version
├── CHANGELOG.md
├── Dockerfile
├── LICENSE
├── Makefile
├── README.md
├── docker-compose.yml
├── docs/
│   ├── api.md
│   ├── architecture.md
│   ├── changelog.md
│   ├── development.md
│   └── planning.md
├── pyproject.toml
├── requirements.txt
├── src/
│   └── docling_service/
│       ├── __init__.py
│       ├── app.py
│       ├── docling_pipeline.py
│       └── llm_enhance.py
└── uv.lock
```

## Como contribuir

1. Crie sua cópia de trabalho
2. Configure `.env` a partir de `.env.example`
3. Faça alterações pequenas e focadas
4. Atualize a documentação correspondente em `README.md` e `docs/`
5. Valide com `docker compose config` e, quando possível, com um teste de conversão real

## Licença

Este projeto está licenciado sob a licença [MIT](LICENSE).

## Observações importantes para compartilhamento

- A aplicação agora segue layout `src/`, mais próximo do fluxo de projetos criados com `uv`.
- O `docker-compose.yml` principal foi mantido portátil; ajustes específicos de máquina, como `build.network: host`, devem ficar em `docker-compose.override.yml` local.
- Helpers locais como `set-env.sh` devem permanecer fora do versionamento.
- A documentação inclui referências às práticas oficiais do GitHub para `.gitignore` e secret scanning.

---
## Apoie

**Feito com ❤️ por Iago Leal** | [☕ Apoie o criador]

Se este projeto ajudou você, considere apoiar:

- Buy Me a Coffee: https://buymeacoffee.com/psiagoleal

<a href="https://buymeacoffee.com/psiagoleal" target="_blank" rel="noopener">
  <img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" height="41" width="174" />
</a>
