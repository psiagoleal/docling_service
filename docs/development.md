# Guia de desenvolvimento

## Estratégia de dependências

O projeto usa dois caminhos complementares:

- `pyproject.toml` + `uv.lock`: desenvolvimento local com `uv`
- `requirements.txt`: build Docker com dependências adequadas ao runtime CUDA

### Desenvolvimento local

```bash
uv python install 3.12
uv sync
uv run uvicorn --app-dir src docling_service.app:app --reload --host 0.0.0.0 --port 8001
```

### Build Docker

```bash
docker compose up -d --build
```

### Override local para rede de build

Se o Docker do seu ambiente precisar usar a rede do host durante o build, crie um arquivo local `docker-compose.override.yml` com:

```yaml
services:
  docling:
    build:
      network: host
```

Esse arquivo deve permanecer fora do versionamento.

## Comandos úteis

```bash
make help
make config
make up
make logs
make health
make smoke FILE=./documento.pdf
```

## Práticas de segurança adotadas

- nunca commitar `.env`
- nunca armazenar chaves reais em scripts do repositório
- nunca versionar logs e saídas geradas
- usar `.env.example` como template público
- preferir segredos injetados pelo ambiente ou pelo provedor de CI/CD

## Checklist antes de publicar mudanças

- [ ] revisar `git status`
- [ ] confirmar que `.env`, logs, overrides locais e artefatos não aparecem como versionados
- [ ] rodar `docker compose config`
- [ ] executar ao menos um smoke test quando a mudança afetar execução
- [ ] atualizar `README.md` e arquivos relevantes em `docs/`
- [ ] revisar `CHANGELOG.md`

## Secret scanning no GitHub

Para reforçar a segurança do repositório publicado, recomenda-se habilitar:

- **Secret Scanning**
- **Push Protection**

Referências oficiais:

- <https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning>
- <https://docs.github.com/en/code-security/secret-scanning/working-with-secret-scanning-and-push-protection>
- <https://docs.github.com/en/get-started/git-basics/ignoring-files>

## Observações sobre helpers locais

Se você usar um helper local como `set-env.sh`, mantenha-o fora do versionamento.

As opções mais seguras são:

- adicionar o arquivo ao `.git/info/exclude` local; ou
- manter uma regra em `.gitignore` se o nome for padronizado para o time.

Em qualquer caso, o helper não deve ser documentado como parte obrigatória do projeto nem carregar segredos que possam parar no histórico Git.
