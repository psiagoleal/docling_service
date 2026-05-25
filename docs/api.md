# API

## Base URL

- Docker padrão: `http://localhost:8010`
- Desenvolvimento local: `http://localhost:8001`

## Endpoints

### `GET /`

Retorna a interface web simples para upload e conversão.

### `GET /health`

Retorna o estado básico do serviço para observabilidade e `HEALTHCHECK` do container.

Exemplo de resposta:

```json
{
  "status": "ok",
  "service": "docling-service"
}
```

### `GET /docs`

Abre a documentação interativa gerada pelo FastAPI.

### `POST /convert`

Converte um único arquivo `PDF` ou `DOCX`.

#### Query params principais

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `format` | `json` ou `zip` | `json` | formato da resposta |
| `enhance` | `bool` | `false` | ativa pós-processamento por LLM |
| `text_provider` | `ollama`, `openai`, `anthropic` | `ollama` | provedor do modelo de texto |
| `vision_provider` | `ollama`, `openai`, `anthropic` | `null` | provedor do modelo de visão |
| `remove_blocks` | `bool` | `false` | remove blocos repetidos por heurística |
| `remove_headers_footers` | `bool` | `true` | remove cabeçalhos/rodapés via LLM |

#### Exemplo

```bash
curl -X POST "http://localhost:8010/convert" \
  -F "file=@documento.pdf"
```

### `POST /convert/batch`

Converte múltiplos arquivos em sequência.

#### Exemplo

```bash
curl -X POST "http://localhost:8010/convert/batch?format=zip" \
  -F "files=@doc1.pdf" \
  -F "files=@doc2.docx" \
  --output batch_result.zip
```

## Formatos de resposta

### JSON

```json
{
  "markdown": "# Documento convertido",
  "assets_dir": "documento_assets"
}
```

### ZIP

Arquivo compactado com `Markdown` e assets gerados.

## Observações

- Para usar `OpenAI` ou `Anthropic`, as variáveis de ambiente correspondentes devem estar definidas.
- Para usar `Ollama`, ajuste `OLLAMA_BASE_URL` conforme o host onde ele estiver rodando.
- Use `/docs` como fonte de verdade para validar schema e parâmetros atuais da API.
