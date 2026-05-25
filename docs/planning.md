# Plano de saneamento do repositório

**Data da auditoria:** 2026-05-25

## Objetivo

Preparar o repositório para compartilhamento com colegas, reduzindo risco de vazamento de segredos, removendo artefatos locais e organizando a documentação mínima de operação e manutenção.

## Achados da auditoria inicial

| Categoria | Evidência encontrada | Risco | Ação aplicada |
|---|---|---|---|
| Segredos / caminhos locais | helper local de ambiente apontava para credenciais externas | Exposição de detalhes do ambiente local e incentivo a uso inseguro de segredos | Helper local mantido fora do versionamento |
| Artefatos locais | `docling_service.log` no diretório raiz | Ruído no repositório e risco de publicar dados de execução | Arquivo removido e `*.log` adicionado ao `.gitignore` |
| Arquivos não utilizados | `web_ui.html` duplicava a UI embutida em `app.py`; `Limits_OpenAI-API.html` era um dump local | Repositório confuso e com conteúdo irrelevante | Arquivos removidos |
| Estrutura desnecessária | diretório `data/` vazio e sem uso efetivo na API | Complexidade desnecessária | Diretório removido e volume correspondente eliminado do `docker-compose.yml` |
| Configuração pouco portátil | `docker-compose.yml` usava porta fixa e `container_name` fixo | Colisão entre ambientes e menor flexibilidade | Compose parametrizado por `.env` com defaults seguros |
| Dependências inconsistentes | `pyproject.toml` não refletia as dependências reais do serviço | `uv sync` não preparava o ambiente corretamente | `pyproject.toml` atualizado para o runtime real da aplicação |
| Fluxo CLI local | `docling_pipeline.py` continha um caminho de teste específico de máquina | Compartilhamento inseguro e comportamento inesperado | Caminho hardcoded substituído por entrada via CLI |

## Decisões adotadas

1. **Configuração por `.env.example`** como mecanismo único e compartilhável.
2. **Segredos fora do versionamento** via `.gitignore` e documentação explícita.
3. **Docker como caminho principal de execução**, com `uv` documentado para desenvolvimento local.
4. **Documentação segmentada** em `README.md` e `docs/`.
5. **Limpeza cirúrgica**: remover apenas o que era claramente local, duplicado ou irrelevante.

## Resultado esperado

Ao final do saneamento, o repositório deve permitir que outra pessoa:

- clone o projeto
- copie `.env.example` para `.env`
- suba o serviço com `docker compose up -d --build`
- consulte a API em `/docs`
- entenda rapidamente arquitetura, endpoints e processo de manutenção

## Próximos passos recomendados

- habilitar **Secret Scanning** e **Push Protection** no GitHub
- adicionar pipeline de CI para validação básica (`docker compose config`, lint e smoke tests)
- adicionar testes automatizados de conversão para arquivos de exemplo pequenos

## Referências externas

- GitHub Docs — Ignoring files: <https://docs.github.com/en/get-started/git-basics/ignoring-files>
- GitHub Docs — About secret scanning: <https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning>
