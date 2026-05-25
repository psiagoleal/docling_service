# Caminho relativo: src/docling_service/llm_enhance.py
"""
LLM Enhancement Module for Docling Conversion Service.

Uses LLMs (local Ollama or cloud APIs) and vision models to post-process
converted Markdown, fixing equations, tables, and other complex elements.

Architecture:
    Original Document → [Vision Model] → Visual analysis (equations, tables, etc.)
    Raw Markdown + Visual analysis → [Text LLM] → Enhanced Markdown

Supported providers:
    - ollama:    Local Ollama instance (text + vision) — via httpx direto
    - openai:    OpenAI API (GPT-4o-mini por padrão)  — via SDK oficial `openai`
    - anthropic: Anthropic API (Claude)               — via SDK oficial `anthropic`

Retry / Rate-limit:
    - Ollama:    sem rate limit (local); httpx com timeout configurável.
    - OpenAI:    o SDK já faz retry com backoff exponencial via `max_retries`.
    - Anthropic: o SDK já faz retry com backoff exponencial via `max_retries`.
"""

# \file src/docling_service/llm_enhance.py
# \brief Módulo de enhancement de Markdown usando LLMs e modelos de visão.
# \author Iago Leal
# \date 2025-07-10

import base64
import io
import os
import re
import time
from pathlib import Path
from typing import Literal, Optional

import httpx

ProviderType = Literal["ollama", "openai", "anthropic"]

# Cache de modelos OpenAI que requerem max_tokens (legado) em vez de
# max_completion_tokens (padrão para modelos novos como gpt-5.4-mini, o3, etc.).
# Preenchido automaticamente por auto-detecção na primeira chamada a cada modelo.
_openai_max_tokens_models: set[str] = set()

# ──────────────────────── Configuração ────────────────────────

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_TEXT_MODEL = os.getenv("OLLAMA_TEXT_MODEL", "qwen3:8b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TEXT_API_KEY = os.getenv("OPENAI_TEXT_API_KEY", "") or OPENAI_API_KEY
OPENAI_VISION_API_KEY = os.getenv("OPENAI_VISION_API_KEY", "") or OPENAI_API_KEY
# gpt-4o-mini: 2.000.000 TPM / 5.000 RPM  (tier 2)
# gpt-4o:        450.000 TPM / 5.000 RPM  (tier 2)
# Use OPENAI_TEXT_MODEL / OPENAI_VISION_MODEL para sobrescrever.
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_TEXT_API_KEY = os.getenv("ANTHROPIC_TEXT_API_KEY", "") or ANTHROPIC_API_KEY
ANTHROPIC_VISION_API_KEY = (
    os.getenv("ANTHROPIC_VISION_API_KEY", "") or ANTHROPIC_API_KEY
)
ANTHROPIC_TEXT_MODEL = os.getenv("ANTHROPIC_TEXT_MODEL", "claude-sonnet-4-20250514")
ANTHROPIC_VISION_MODEL = os.getenv("ANTHROPIC_VISION_MODEL", "claude-sonnet-4-20250514")

LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "600"))
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# Limite de tokens de saída por requisição.
# 16384 acomoda documentos com muitas equações LaTeX (que ocupam mais espaço).
# Reduza via LLM_MAX_TOKENS se precisar economizar TPM.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16384"))

# Tamanho do lote de imagens por requisição de visão.
# Lotes menores reduzem o consumo de TPM por chamada.
VISION_BATCH_SIZE = int(os.getenv("VISION_BATCH_SIZE", "3"))

# Pausa (segundos) entre lotes de visão para aliviar o rate limit de TPM.
# Com gpt-4o-mini (2M TPM) um valor de 10s é conservador e seguro.
VISION_BATCH_SLEEP = float(os.getenv("VISION_BATCH_SLEEP", "10.0"))

# ── Enhancement por chunks (documentos grandes) ────────────────────────
# Documentos acima de ENHANCE_CHUNK_SIZE caracteres são divididos em seções
# menores, processadas individualmente e remontadas ao final.
# Isso evita timeouts em documentos longos (ex.: 35+ páginas).
ENHANCE_CHUNK_SIZE = int(os.getenv("ENHANCE_CHUNK_SIZE", "8000"))

# Pausa (segundos) entre chunks de enhancement para respeitar rate limits.
ENHANCE_CHUNK_SLEEP = float(os.getenv("ENHANCE_CHUNK_SLEEP", "5.0"))

# Controla se cabeçalhos e rodapés de página são removidos durante o enhancement.
# Padrão: True (remove cabeçalhos e rodapés de página do Markdown final).
# Pode ser desativado via variável de ambiente LLM_REMOVE_HEADERS_FOOTERS=false
# ou por chamada direta via parâmetro remove_headers_footers em enhance_markdown().
REMOVE_HEADERS_FOOTERS = os.getenv("LLM_REMOVE_HEADERS_FOOTERS", "true").lower() in (
    "true",
    "1",
    "yes",
)


def _get_default_model(provider: ProviderType, kind: str) -> str:
    """Retorna o modelo padrão para o provedor e tipo (text/vision) informados."""
    defaults = {
        ("ollama", "text"): OLLAMA_TEXT_MODEL,
        ("ollama", "vision"): OLLAMA_VISION_MODEL,
        ("openai", "text"): OPENAI_TEXT_MODEL,
        ("openai", "vision"): OPENAI_VISION_MODEL,
        ("anthropic", "text"): ANTHROPIC_TEXT_MODEL,
        ("anthropic", "vision"): ANTHROPIC_VISION_MODEL,
    }
    return defaults[(provider, kind)]


# ──────────────────────── PDF → Imagens ────────────────────────


def _pdf_pages_to_images(
    pdf_path: str,
    max_pages: int = 0,
    dpi: int = 150,
    image_format: str = "JPEG",
) -> list[bytes]:
    """
    Converte páginas de um PDF em imagens (bytes).

    Args:
        pdf_path:     Caminho para o arquivo PDF.
        max_pages:    Número máximo de páginas (0 = todas).
        dpi:          Resolução de renderização.
        image_format: Formato de saída (JPEG ou PNG).

    Returns:
        Lista de bytes representando cada página como imagem.
    """
    from pdf2image import convert_from_path

    kwargs: dict = {"dpi": dpi, "first_page": 1}
    if max_pages > 0:
        kwargs["last_page"] = max_pages

    images = convert_from_path(pdf_path, **kwargs)
    result: list[bytes] = []
    for img in images:
        buf = io.BytesIO()
        save_kwargs: dict = {"format": image_format}
        if image_format.upper() == "JPEG":
            img = img.convert("RGB")  # JPEG não suporta canal alpha
            save_kwargs["quality"] = 85
        img.save(buf, **save_kwargs)
        result.append(buf.getvalue())
    return result


def _image_to_b64(image_bytes: bytes) -> str:
    """Codifica bytes de imagem em Base64 (string UTF-8)."""
    return base64.b64encode(image_bytes).decode("utf-8")


# ──────────────────────── Chamadas aos provedores LLM ────────────────────────


def _call_ollama(messages: list[dict], model: str) -> str:
    """
    Chama a API local do Ollama via httpx.

    Ollama roda localmente, sem rate limit, portanto não necessita de
    retry automático elaborado — apenas um timeout generoso.
    """
    with httpx.Client(timeout=LLM_TIMEOUT) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def _call_openai(messages: list[dict], model: str, api_key: str = "") -> str:
    """
    Chama a API da OpenAI usando o SDK oficial.

    O cliente `openai.OpenAI` gerencia automaticamente:
    - Retry com backoff exponencial (controlado por `max_retries`)
    - Respeito ao header `Retry-After` em respostas 429 (RateLimitError)
    - Timeout configurável por requisição

    Compatibilidade de parâmetros de tokens:
    - Modelos novos (gpt-5.4-mini, o3, etc.) usam `max_completion_tokens`.
    - Modelos legados (gpt-4o-mini, gpt-4o, etc.) usam `max_tokens`.
    - A detecção é automática: tenta `max_completion_tokens` primeiro e,
      se o modelo rejeitar, alterna para `max_tokens` e cacheia a preferência.

    Args:
        messages: Lista de mensagens no formato OpenAI Chat.
        model:    ID do modelo (ex.: "gpt-4o", "gpt-5.4-mini").
        api_key:  Chave de API (opcional; usa variável de ambiente se omitida).

    Returns:
        Conteúdo textual da resposta do modelo.

    Raises:
        ImportError:  Se o pacote `openai` não estiver instalado.
        ValueError:   Se nenhuma chave de API for encontrada.
        openai.APIError: Em caso de falha persistente após os retries.
    """
    try:
        from openai import BadRequestError as OpenAIBadRequestError
        from openai import OpenAI
        from openai import RateLimitError as OpenAIRateLimitError
    except ImportError as exc:
        raise ImportError(
            "Pacote 'openai' não encontrado. Instale com: uv add openai"
        ) from exc

    key = api_key or OPENAI_TEXT_API_KEY
    if not key:
        raise ValueError(
            "Chave de API OpenAI não configurada. "
            "Defina a variável de ambiente OPENAI_API_KEY ou OPENAI_TEXT_API_KEY."
        )

    # max_retries=0: retry gerenciado manualmente para distinguir
    # insufficient_quota (saldo esgotado) de rate_limit_exceeded (velocidade).
    # O SDK com max_retries>0 retentaria ambos igualmente, desperdiçando tempo
    # em casos de billing onde nenhum retry vai resolver o problema.
    client = OpenAI(
        api_key=key,
        timeout=float(LLM_TIMEOUT),
        max_retries=0,
    )

    # Auto-detecção: modelos novos usam max_completion_tokens,
    # legados usam max_tokens. Cacheia resultado por modelo.
    use_legacy = model in _openai_max_tokens_models
    token_param_switched = False
    attempt = 0

    while attempt <= MAX_RETRIES:
        try:
            create_kwargs: dict = {
                "model": model,
                "messages": messages,  # type: ignore[arg-type]
            }
            if use_legacy:
                create_kwargs["max_tokens"] = MAX_TOKENS
            else:
                create_kwargs["max_completion_tokens"] = MAX_TOKENS

            response = client.chat.completions.create(**create_kwargs)
            return response.choices[0].message.content or ""

        except OpenAIBadRequestError as e:
            # Auto-detecção de parâmetro de tokens: se o modelo rejeitar
            # max_tokens ou max_completion_tokens, alterna automaticamente.
            if "unsupported_parameter" in str(e) and not token_param_switched:
                use_legacy = not use_legacy
                token_param_switched = True
                if use_legacy:
                    _openai_max_tokens_models.add(model)
                    print(f"  Modelo {model}: alternando para max_tokens (legado)")
                else:
                    _openai_max_tokens_models.discard(model)
                    print(f"  Modelo {model}: alternando para max_completion_tokens")
                continue  # Retry imediato, não conta como tentativa
            raise

        except OpenAIRateLimitError as e:
            # insufficient_quota: saldo de créditos esgotado — retry não resolve
            if getattr(e, "code", None) == "insufficient_quota":
                raise RuntimeError(
                    "Cota OpenAI esgotada (insufficient_quota).\n"
                    "Este é um problema de CRÉDITOS, não de rate limit de velocidade.\n"
                    "Adicione créditos em: "
                    "https://platform.openai.com/settings/organization/billing"
                ) from e
            # rate_limit_exceeded: backoff exponencial e nova tentativa
            if attempt >= MAX_RETRIES:
                raise
            wait = 5 * (2**attempt)  # 5 s → 10 s → 20 s
            print(
                f"  OpenAI rate limit (429): aguardando {wait}s "
                f"(tentativa {attempt + 1}/{MAX_RETRIES})..."
            )
            time.sleep(wait)

        attempt += 1

    return ""  # unreachable


def _call_anthropic(messages: list[dict], model: str, api_key: str = "") -> str:
    """
    Chama a API da Anthropic usando o SDK oficial.

    O cliente `anthropic.Anthropic` gerencia automaticamente:
    - Retry com backoff exponencial (controlado por `max_retries`)
    - Respeito ao header `Retry-After` em respostas 429 (RateLimitError)
    - Timeout configurável por requisição

    A Anthropic separa mensagens de sistema das demais; esta função extrai
    automaticamente entradas com `role == "system"` e as passa no campo
    `system` da requisição.

    Args:
        messages: Lista de mensagens (pode incluir role "system").
        model:    ID do modelo (ex.: "claude-sonnet-4-20250514").
        api_key:  Chave de API (opcional; usa variável de ambiente se omitida).

    Returns:
        Conteúdo textual concatenado dos blocos de texto da resposta.

    Raises:
        ImportError:      Se o pacote `anthropic` não estiver instalado.
        ValueError:       Se nenhuma chave de API for encontrada.
        anthropic.APIError: Em caso de falha persistente após os retries.
    """
    try:
        from anthropic import Anthropic
        from anthropic import RateLimitError as AnthropicRateLimitError
    except ImportError as exc:
        raise ImportError(
            "Pacote 'anthropic' não encontrado. Instale com: uv add anthropic"
        ) from exc

    key = api_key or ANTHROPIC_TEXT_API_KEY
    if not key:
        raise ValueError(
            "Chave de API Anthropic não configurada. "
            "Defina a variável de ambiente ANTHROPIC_API_KEY ou ANTHROPIC_TEXT_API_KEY."
        )

    # A Anthropic exige que mensagens de sistema sejam passadas separadamente
    system_parts: list[str] = []
    user_messages: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            content = m["content"]
            if isinstance(content, str) and content:
                system_parts.append(content)
        else:
            user_messages.append(m)

    # max_retries=0: retry gerenciado manualmente com backoff explícito.
    client = Anthropic(
        api_key=key,
        timeout=float(LLM_TIMEOUT),
        max_retries=0,
    )

    create_kwargs: dict = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": user_messages,  # type: ignore[arg-type]
    }
    if system_parts:
        create_kwargs["system"] = "\n\n".join(system_parts)

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.messages.create(**create_kwargs)
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except AnthropicRateLimitError as e:
            if attempt >= MAX_RETRIES:
                raise
            wait = 5 * (2**attempt)  # 5 s → 10 s → 20 s
            print(
                f"  Anthropic rate limit (429): aguardando {wait}s "
                f"(tentativa {attempt + 1}/{MAX_RETRIES})..."
            )
            time.sleep(wait)

    return ""  # unreachable


def _call_llm(messages: list[dict], provider: ProviderType, model: str) -> str:
    """
    Despacha a chamada ao provedor LLM correto.

    Args:
        messages: Lista de mensagens no formato do provedor.
        provider: Identificador do provedor ("ollama", "openai" ou "anthropic").
        model:    ID do modelo a ser utilizado.

    Returns:
        Resposta textual do modelo.

    Raises:
        ValueError: Se o provedor for desconhecido.
    """
    if provider == "ollama":
        return _call_ollama(messages, model)
    elif provider == "openai":
        return _call_openai(messages, model)
    elif provider == "anthropic":
        return _call_anthropic(messages, model)
    raise ValueError(f"Provider desconhecido: {provider}")


# ──────────────────────── Análise Visual ────────────────────────

VISION_SYSTEM = (
    "You are an expert document analysis assistant. "
    "Your PRIMARY mission is to extract every mathematical equation and formula "
    "from the document pages with complete accuracy in LaTeX notation.\n\n"
    "## EQUATIONS AND FORMULAS (HIGHEST PRIORITY)\n"
    "This is your most important task. The automatic PDF converter often FAILS "
    "to decode equations, producing placeholder markers like '<!-- formula-not-decoded -->'. "
    "You must provide the correct LaTeX for every equation visible on the page.\n"
    "For each equation or formula:\n"
    "- Write the COMPLETE LaTeX representation\n"
    "- Indicate if it is inline (within text) or display (standalone/centered)\n"
    "- Include the surrounding context (a few words before/after) so the equation "
    "can be matched to its correct position in the document\n"
    "- Number each equation sequentially within the page as EQ1, EQ2, etc.\n"
    "Format:\n"
    "  EQ1 [display]: $$E = mc^2$$ (after 'the famous equation' / before 'was proposed')\n"
    "  EQ2 [inline]: $\\alpha + \\beta$ (after 'where the sum' / before 'represents')\n"
    "If there are no equations on this page, write: EQUATIONS: none\n\n"
    "## Page Header\n"
    "Examine the very top margin of the page — the area above the main text body. "
    "A page header is text placed in this top margin region, typically containing: "
    "a running document title, chapter name, section name, date, or page number at the top.\n"
    "Report it as: [HEADER]: <exact text content>\n"
    "If no header is present, write: [HEADER]: none\n\n"
    "## Page Footer\n"
    "Examine the very bottom margin of the page — the area below the main text body. "
    "A page footer is text placed in this bottom margin region, typically containing: "
    "page numbers, copyright notices, company or institution name, or document reference codes.\n"
    "Report it as: [FOOTER]: <exact text content>\n"
    "If no footer is present, write: [FOOTER]: none\n\n"
    "## Tables\n"
    "For each table: describe structure (rows, columns, header row, cell content).\n\n"
    "## Figures and Charts\n"
    "For each figure or chart: brief description of what it shows.\n\n"
    "Be precise and exhaustive. Analyze only what is visually present on the page."
)

VISION_PAGE = (
    "Analyze this document page (page {page_num}). "
    "Structure your response using the following sections:\n\n"
    "EQUATIONS:\n"
    "  EQ1 [display/inline]: <LaTeX> (after '<context before>' / before '<context after>')\n"
    "  ... or 'none' if no equations\n\n"
    "[HEADER]: <header text, or 'none' if absent>\n"
    "[FOOTER]: <footer text, or 'none' if absent>\n"
    "TABLES: <each table structure and content, or 'none'>\n"
    "FIGURES: <brief description of each figure, or 'none'>"
)

VISION_BATCH_PROMPT = (
    "Analyze all pages shown above, in page order. "
    "For each page, organize your response as:\n\n"
    "## Page N\n"
    "EQUATIONS:\n"
    "  EQ1 [display/inline]: <LaTeX> (after '<context before>' / before '<context after>')\n"
    "  ... or 'none' if no equations\n\n"
    "[HEADER]: <header text, or 'none' if absent>\n"
    "[FOOTER]: <footer text, or 'none' if absent>\n"
    "TABLES: <each table structure and content, or 'none'>\n"
    "FIGURES: <brief description of each figure, or 'none'>"
)


def _vision_ollama(images: list[bytes]) -> str:
    """
    Analisa páginas com o modelo de visão do Ollama.

    Ollama processa uma imagem por chamada (modelos multimodais locais).
    """
    analyses: list[str] = []
    for i, img in enumerate(images):
        msgs = [
            {"role": "system", "content": VISION_SYSTEM},
            {
                "role": "user",
                "content": VISION_PAGE.format(page_num=i + 1),
                "images": [_image_to_b64(img)],
            },
        ]
        print(f"  Visão Ollama: analisando página {i + 1}/{len(images)}...")
        text = _call_ollama(msgs, _get_default_model("ollama", "vision"))
        analyses.append(f"### Page {i + 1}\n{text}")
    return "\n\n".join(analyses)


def _vision_openai(images: list[bytes], model: str) -> str:
    """
    Analisa páginas com a API de visão da OpenAI (via SDK oficial).

    Processa em lotes para evitar payloads excessivamente grandes.
    O SDK cuida automaticamente de retries em caso de 429.
    """
    all_analyses: list[str] = []

    for batch_start in range(0, len(images), VISION_BATCH_SIZE):
        batch = images[batch_start : batch_start + VISION_BATCH_SIZE]
        batch_end = batch_start + len(batch)
        print(
            f"  Visão OpenAI: processando páginas "
            f"{batch_start + 1}-{batch_end}/{len(images)}..."
        )

        content: list[dict] = []
        for i, img in enumerate(batch):
            page_num = batch_start + i + 1
            content.append({"type": "text", "text": f"--- Page {page_num} ---"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{_image_to_b64(img)}",
                        "detail": "high",
                    },
                }
            )
        content.append({"type": "text", "text": VISION_BATCH_PROMPT})

        messages = [
            {"role": "system", "content": VISION_SYSTEM},
            {"role": "user", "content": content},
        ]
        result = _call_openai(messages, model, api_key=OPENAI_VISION_API_KEY)
        all_analyses.append(result)

        if batch_end < len(images):
            time.sleep(VISION_BATCH_SLEEP)  # pausa entre lotes para respeitar o TPM

    return "\n\n".join(all_analyses)


def _vision_anthropic(images: list[bytes], model: str) -> str:
    """
    Analisa páginas com a API de visão da Anthropic (via SDK oficial).

    Processa em lotes para gerenciar o tamanho do payload.
    O SDK cuida automaticamente de retries em caso de 429.
    """
    all_analyses: list[str] = []

    for batch_start in range(0, len(images), VISION_BATCH_SIZE):
        batch = images[batch_start : batch_start + VISION_BATCH_SIZE]
        batch_end = batch_start + len(batch)
        print(
            f"  Visão Anthropic: processando páginas "
            f"{batch_start + 1}-{batch_end}/{len(images)}..."
        )

        content: list[dict] = []
        for i, img in enumerate(batch):
            page_num = batch_start + i + 1
            content.append({"type": "text", "text": f"--- Page {page_num} ---"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": _image_to_b64(img),
                    },
                }
            )
        content.append({"type": "text", "text": VISION_BATCH_PROMPT})

        messages = [
            {"role": "system", "content": VISION_SYSTEM},
            {"role": "user", "content": content},
        ]
        result = _call_anthropic(messages, model, api_key=ANTHROPIC_VISION_API_KEY)
        all_analyses.append(result)

        if batch_end < len(images):
            time.sleep(VISION_BATCH_SLEEP)  # pausa entre lotes para respeitar o TPM

    return "\n\n".join(all_analyses)


def analyze_with_vision(
    images: list[bytes],
    provider: ProviderType,
    model: Optional[str] = None,
) -> str:
    """
    Envia imagens de páginas do documento a um modelo de visão e retorna a análise.

    Args:
        images:   Lista de bytes de imagens (uma por página).
        provider: Provedor de visão ("ollama", "openai" ou "anthropic").
        model:    Modelo override (usa padrão do provedor se omitido).

    Returns:
        String com a análise visual de todas as páginas.
    """
    if not images:
        return ""

    model = model or _get_default_model(provider, "vision")
    print(f"Análise visual: enviando {len(images)} página(s) para {provider}/{model}")

    if provider == "ollama":
        return _vision_ollama(images)
    elif provider == "openai":
        return _vision_openai(images, model)
    elif provider == "anthropic":
        return _vision_anthropic(images, model)

    raise ValueError(f"Provider desconhecido: {provider}")


# ──────────────────────── Enhancement de Texto ────────────────────────

# ── Funções auxiliares para chunking ────────────────────────────────────────


def _split_markdown_chunks(markdown: str, max_chars: int) -> list[str]:
    """
    Divide o Markdown em chunks de aproximadamente max_chars caracteres,
    cortando em fronteiras naturais (linhas em branco, headings).

    Preserva a integridade de parágrafos, tabelas e blocos de código:
    nunca corta no meio de uma linha.

    Args:
        markdown:  Texto Markdown completo.
        max_chars: Tamanho máximo aproximado de cada chunk em caracteres.

    Returns:
        Lista de strings, cada uma representando um chunk do documento.
    """
    if len(markdown) <= max_chars:
        return [markdown]

    chunks: list[str] = []
    lines = markdown.split("\n")
    current_chunk: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 para o \n

        # Se adicionar esta linha excede o limite E já temos conteúdo,
        # tenta cortar em uma fronteira natural.
        if current_len + line_len > max_chars and current_chunk:
            # Fronteiras naturais para corte (ordem de preferência):
            # 1. Heading (# ...)
            # 2. Linha em branco
            is_boundary = line.startswith("#") or line.strip() == ""

            if is_boundary or current_len >= max_chars:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0

        current_chunk.append(line)
        current_len += line_len

    # Último chunk
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


# ── Filtragem de notas visuais por chunk ────────────────────────────────────


def _parse_vision_pages(vision_notes: str) -> dict[int, str]:
    """
    Extrai as notas visuais por número de página.

    Parseia as seções ``## Page N`` (ou ``### Page N``) das notas visuais
    e retorna um dicionário {número_da_página: texto_da_seção}.

    Args:
        vision_notes: Texto completo das notas visuais.

    Returns:
        Dicionário mapeando número de página → notas daquela página.
    """
    if not vision_notes:
        return {}

    pages: dict[int, str] = {}
    page_pattern = re.compile(r"^#{2,3}\s+Page\s+(\d+)", re.MULTILINE)
    matches = list(page_pattern.finditer(vision_notes))

    if not matches:
        # Notas sem estrutura de página reconhecível → retorna vazio
        # (o caller usará as notas completas como fallback)
        return {}

    for i, match in enumerate(matches):
        page_num = int(match.group(1))
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(vision_notes)
        pages[page_num] = vision_notes[start:end].strip()

    return pages


def _vision_notes_for_chunk(
    all_pages: dict[int, str],
    chunk_index: int,
    total_chunks: int,
    total_pages: int,
    margin: int = 2,
) -> str:
    """
    Retorna as notas visuais relevantes para um chunk específico.

    Estima quais páginas correspondem ao chunk baseado na posição proporcional
    no documento, com uma margem de segurança (± margin páginas).

    Args:
        all_pages:    Dicionário {page_num: notes} de todas as páginas.
        chunk_index:  Índice do chunk atual (0-based).
        total_chunks: Total de chunks do documento.
        total_pages:  Total de páginas do documento.
        margin:       Páginas extras antes/depois para margem de segurança.

    Returns:
        String com as notas visuais filtradas para o chunk.
    """
    if not all_pages or total_pages == 0:
        return ""

    # Estima o range de páginas para este chunk
    pages_per_chunk = total_pages / total_chunks
    page_start = max(1, int(chunk_index * pages_per_chunk) + 1 - margin)
    page_end = min(total_pages, int((chunk_index + 1) * pages_per_chunk) + margin)

    relevant: list[str] = []
    for page_num in range(page_start, page_end + 1):
        if page_num in all_pages:
            relevant.append(all_pages[page_num])

    return "\n\n".join(relevant)


# ── Blocos que compõem o prompt de enhancement ──────────────────────────────

_ENHANCE_BASE = (
    "You are an expert document conversion specialist. You receive a Markdown document "
    "that was automatically converted from a PDF or DOCX file, along with optional "
    "visual analysis notes from the original document.\n\n"
    "Your task is to produce a corrected version of the Markdown that:\n\n"
    "1. **EQUATIONS (CRITICAL)**: Fix and preserve ALL mathematical equations.\n"
    "   - Convert each equation to proper LaTeX notation "
    "($...$ for inline math, $$...$$ for display math).\n"
    "   - The automatic converter frequently FAILS to decode equations and inserts "
    "the placeholder `<!-- formula-not-decoded -->` where an equation should be.\n"
    "   - You MUST find every `<!-- formula-not-decoded -->` marker in the Markdown "
    "and replace it with the correct LaTeX equation from the visual analysis notes.\n"
    "   - Cross-reference the visual analysis notes (EQ1, EQ2, etc. with context clues) "
    "to identify which equation belongs at each placeholder position.\n"
    "   - Also fix any equations that were partially decoded or corrupted.\n"
    "   - NEVER leave a `<!-- formula-not-decoded -->` marker in the output.\n"
    "   - NEVER omit, skip, or remove an equation from the output.\n\n"
    "2. **HEADERS AND FOOTERS**: Remove all page headers and footers.\n"
    "   - A page HEADER is text in the top margin of each page "
    "(running document title, chapter name, section name, or page number at top).\n"
    "   - A page FOOTER is text in the bottom margin of each page "
    "(page numbers, copyright notices, company name, or document reference codes).\n"
    "   - These are navigation/printing aids and must NOT appear in the body text.\n"
    "   - Use [HEADER] and [FOOTER] markers from the visual analysis notes to locate them.\n"
    "   - If no visual analysis is available, use contextual judgment: short lines with "
    "only page numbers, or document titles repeated at regular intervals, are indicators.\n"
    "   - CRITICAL: Mathematical content is NEVER a header or footer — never remove "
    "equations, formulas, or any LaTeX expression, even if they appear as short lines.\n\n"
    "3. Correct OCR artifacts and conversion errors.\n"
    "4. Fix table formatting to proper Markdown tables.\n"
    "5. Remove conversion artifacts (broken lines, misplaced characters).\n"
    "6. Preserve the original language of the document.\n"
    "7. Preserve ALL original body content — do not remove or summarize any text "
    "that belongs to the document body.\n"
    "8. **PARAGRAPH INTEGRITY**: Your output MUST contain every paragraph from the "
    "input. You are only allowed to:\n"
    "   - Fix formatting, OCR errors, and broken lines within paragraphs\n"
    "   - Replace `<!-- formula-not-decoded -->` placeholders with LaTeX equations\n"
    "   - Remove lines that are clearly page headers or footers\n"
    "   You must NEVER delete, skip, merge, or summarize body paragraphs. "
    "If unsure whether something is body content or a header/footer, KEEP IT.\n"
)

_ENHANCE_CLOSING = (
    "\nIMPORTANT:\n"
    "- Return ONLY the corrected Markdown. "
    "No explanations, no preamble, no wrapping in code blocks.\n"
    "- Your output MUST preserve every body paragraph from the input. "
    "The output length should be approximately the same as the input "
    "(or longer if equations are expanded). NEVER summarize or condense the text.\n"
    "- If you are unsure about any content, KEEP IT unchanged rather than removing it."
)


def _build_enhance_system(remove_headers_footers: bool = True) -> str:
    """
    Constrói o prompt de sistema para o LLM de enhancement.

    Args:
        remove_headers_footers: Mantido para compatibilidade de API.
                                Cabeçalhos e rodapés são sempre removidos.

    Returns:
        String com o prompt de sistema completo.
    """
    return _ENHANCE_BASE + _ENHANCE_CLOSING


ENHANCE_SYSTEM = _build_enhance_system()


def _enhance_single_chunk(
    chunk: str,
    vision_notes: str,
    provider: ProviderType,
    model: str,
    remove_headers_footers: bool,
    chunk_index: int = 0,
    total_chunks: int = 1,
) -> str:
    """
    Processa um único chunk de Markdown pelo LLM de enhancement.

    Args:
        chunk:                  Trecho de Markdown a ser corrigido.
        vision_notes:           Análise visual completa (todas as páginas).
        provider:               Provedor de texto.
        model:                  Modelo do LLM.
        remove_headers_footers: Se True, remove cabeçalhos/rodapés.
        chunk_index:            Índice do chunk atual (0-based).
        total_chunks:           Total de chunks do documento.

    Returns:
        Chunk de Markdown corrigido.
    """
    formula_placeholders = chunk.count("<!-- formula-not-decoded -->")

    # Monta contexto adequado para chunk vs documento inteiro
    if total_chunks > 1:
        context_header = (
            f"You are processing SECTION {chunk_index + 1} of {total_chunks} "
            f"of a larger document.\n"
            f"RULES FOR SECTION PROCESSING:\n"
            f"- Output EVERY paragraph and line of body text from this section.\n"
            f"- Do NOT add content from other sections.\n"
            f"- Do NOT add introductions or conclusions not present in this section.\n"
            f"- Do NOT summarize, merge, or skip any body paragraph.\n"
            f"- The visual analysis notes may cover pages beyond this section — "
            f"use them ONLY to match equations and identify headers/footers "
            f"for the text in THIS section.\n\n"
        )
    else:
        context_header = ""

    parts = [
        context_header,
        "Here is the automatically converted Markdown"
        + (" section" if total_chunks > 1 else " document")
        + ":\n",
        "---BEGIN MARKDOWN---",
        chunk,
        "---END MARKDOWN---",
    ]

    if formula_placeholders > 0:
        parts.append(
            f"\n\nIMPORTANT: This section contains {formula_placeholders} "
            f"'<!-- formula-not-decoded -->' placeholder(s) where the automatic "
            f"converter failed to decode equations. You MUST replace each one "
            f"with the correct LaTeX equation using the visual analysis notes below."
        )

    if vision_notes:
        parts.extend(
            [
                "\n\nHere are analysis notes from visual inspection of the original document pages.",
                "The notes include numbered equations (EQ1, EQ2, etc.) with context clues,",
                "and [HEADER]/[FOOTER] markers identifying page navigation elements.",
                "Use the surrounding text context to match equations to their correct positions.",
                "---BEGIN VISUAL ANALYSIS---",
                vision_notes,
                "---END VISUAL ANALYSIS---",
                "\nUse these notes to:",
                "- Replace every `<!-- formula-not-decoded -->` marker with the correct LaTeX equation",
                "- Fix any corrupted or partial equations",
                "- Identify and remove page headers and footers",
            ]
        )
    elif formula_placeholders > 0:
        parts.append(
            "\nWARNING: No visual analysis notes are available, but formula placeholders exist. "
            "Try to infer equations from surrounding context, or leave a descriptive "
            "LaTeX comment like $\\text{[equation not available]}$ — but NEVER leave "
            "the raw `<!-- formula-not-decoded -->` marker in the output."
        )

    parts.append("\nPlease produce the corrected Markdown now.")

    messages = [
        {"role": "system", "content": _build_enhance_system(remove_headers_footers)},
        {"role": "user", "content": "\n".join(parts)},
    ]

    result = _call_llm(messages, provider, model)

    # Remove bloco de código caso o LLM tenha adicionado um wrapper ```markdown
    result = re.sub(r"^```(?:markdown)?\s*\n", "", result)
    result = re.sub(r"\n```\s*$", "", result)
    return result.strip()


def enhance_with_text_llm(
    markdown: str,
    vision_notes: str = "",
    provider: ProviderType = "ollama",
    model: Optional[str] = None,
    remove_headers_footers: bool = False,
) -> str:
    """
    Envia o Markdown (e notas visuais opcionais) a um LLM de texto para enhancement.

    Documentos grandes são automaticamente divididos em chunks de
    ENHANCE_CHUNK_SIZE caracteres e processados individualmente para
    evitar timeouts.

    Args:
        markdown:               Markdown bruto gerado pela conversão.
        vision_notes:           Análise visual das páginas originais (opcional).
        provider:               Provedor de texto ("ollama", "openai" ou "anthropic").
        model:                  Modelo override (usa padrão do provedor se omitido).
        remove_headers_footers: Se True, instrui o LLM a remover cabeçalhos e
                                rodapés de página do Markdown final.

    Returns:
        Markdown corrigido e aprimorado pelo LLM.
    """
    model = model or _get_default_model(provider, "text")
    print(f"Enhancement de texto: usando {provider}/{model}")

    # Count formula-not-decoded markers
    formula_placeholders = markdown.count("<!-- formula-not-decoded -->")
    if formula_placeholders > 0:
        print(
            f"  Encontrados {formula_placeholders} marcador(es) "
            f"'<!-- formula-not-decoded -->' no Markdown"
        )

    # ── Dividir em chunks se o documento for grande ─────────────────────
    chunks = _split_markdown_chunks(markdown, ENHANCE_CHUNK_SIZE)

    if len(chunks) == 1:
        # Documento pequeno: processar inteiro (comportamento original)
        print(f"  Documento pequeno ({len(markdown)} chars): processando inteiro")
        return _enhance_single_chunk(
            chunks[0],
            vision_notes,
            provider,
            model,
            remove_headers_footers,
        )

    # ── Documento grande: processar por chunks ──────────────────────────

    # Parseia notas visuais por página para filtragem por chunk
    vision_pages = _parse_vision_pages(vision_notes) if vision_notes else {}
    total_pages = max(vision_pages.keys()) if vision_pages else 0

    if vision_pages:
        print(
            f"  Notas visuais parseadas: {len(vision_pages)} página(s) detectadas "
            f"(filtragem por chunk ativa)"
        )
    elif vision_notes:
        print(
            "  Notas visuais sem estrutura '## Page N' — "
            "enviando notas completas para cada chunk (fallback)"
        )

    print(
        f"  Documento grande ({len(markdown)} chars): "
        f"dividido em {len(chunks)} chunks "
        f"(~{ENHANCE_CHUNK_SIZE} chars cada)"
    )

    enhanced_chunks: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_formulas = chunk.count("<!-- formula-not-decoded -->")
        print(
            f"  Processando chunk {i + 1}/{len(chunks)} "
            f"({len(chunk)} chars"
            + (f", {chunk_formulas} fórmula(s)" if chunk_formulas > 0 else "")
            + ")..."
        )

        # Filtra notas visuais relevantes para este chunk
        if vision_pages:
            chunk_notes = _vision_notes_for_chunk(
                vision_pages, i, len(chunks), total_pages
            )
        else:
            chunk_notes = vision_notes  # fallback: notas completas

        result = _enhance_single_chunk(
            chunk,
            chunk_notes,
            provider,
            model,
            remove_headers_footers,
            chunk_index=i,
            total_chunks=len(chunks),
        )

        # ── Validação por chunk: fallback se conteúdo perdido ───────────
        ratio = len(result) / len(chunk) if len(chunk) > 0 else 1.0
        if ratio < 0.6:
            print(
                f"  ⚠ Chunk {i + 1}: resultado muito curto "
                f"({len(result)} vs {len(chunk)} chars, ratio={ratio:.2f}). "
                f"Usando chunk original."
            )
            enhanced_chunks.append(chunk)
        else:
            enhanced_chunks.append(result)

        # Pausa entre chunks para respeitar rate limits
        if i < len(chunks) - 1:
            time.sleep(ENHANCE_CHUNK_SLEEP)

    return "\n\n".join(enhanced_chunks)


# ──────────────────────── Orquestrador Principal ────────────────────────


def enhance_markdown(
    markdown_text: str,
    original_path: Optional[str] = None,
    text_provider: ProviderType = "ollama",
    text_model: Optional[str] = None,
    vision_provider: Optional[ProviderType] = None,
    vision_model: Optional[str] = None,
    max_pages_vision: int = 0,
    remove_headers_footers: bool = REMOVE_HEADERS_FOOTERS,
) -> str:
    """
    Aprimora o Markdown convertido usando LLMs e análise visual opcional.

    Fluxo:
        1. (Opcional) Converte o documento original em imagens de página.
        2. (Opcional) Envia as imagens ao modelo de visão para extrair notas.
        3. Envia o Markdown bruto + notas visuais ao LLM de texto para enhancement.

    Args:
        markdown_text:          Markdown bruto gerado pela conversão.
        original_path:          Caminho para o arquivo original (PDF ou DOCX).
        text_provider:          Provedor para o LLM de texto.
        text_model:             Modelo override para o LLM de texto.
        vision_provider:        Provedor para o modelo de visão (None = pular visão).
        vision_model:           Modelo override para o modelo de visão.
        max_pages_vision:       Máximo de páginas a enviar ao modelo de visão (0 = todas).
        remove_headers_footers: Se True, remove cabeçalhos e rodapés de página do
                                Markdown final. Padrão lido da variável de ambiente
                                LLM_REMOVE_HEADERS_FOOTERS (true por padrão).

    Returns:
        Markdown aprimorado, ou o original em caso de falha.
    """
    vision_notes = ""

    # ── Passo 1: Análise visual (opcional) ──────────────────────────────────
    if vision_provider and original_path:
        pdf_for_vision = original_path
        ext = Path(original_path).suffix.lower()

        # Converte DOCX → PDF para viabilizar a análise visual por páginas
        if ext == ".docx":
            try:
                from .docling_pipeline import _docx_to_pdf

                pdf_for_vision = str(_docx_to_pdf(Path(original_path)))
                print("DOCX convertido para PDF para análise visual")
            except Exception as e:
                print(f"Aviso: não foi possível converter DOCX→PDF para visão ({e})")
                pdf_for_vision = None  # type: ignore[assignment]

        if pdf_for_vision and Path(pdf_for_vision).suffix.lower() == ".pdf":
            try:
                images = _pdf_pages_to_images(
                    pdf_for_vision, max_pages=max_pages_vision
                )
                vision_notes = analyze_with_vision(
                    images, vision_provider, vision_model
                )
                print(
                    f"Análise visual concluída: {len(vision_notes)} caracteres de notas"
                )
            except Exception as e:
                print(f"Aviso: análise visual falhou ({e}). Continuando sem visão.")

    # ── Passo 2: Enhancement via LLM de texto ───────────────────────────────
    try:
        enhanced = enhance_with_text_llm(
            markdown_text,
            vision_notes=vision_notes,
            provider=text_provider,
            model=text_model,
            remove_headers_footers=remove_headers_footers,
        )

        # Sanidade: se o output for muito mais curto, o LLM provavelmente resumiu
        if len(enhanced) < len(markdown_text) * 0.5:
            print(
                "Aviso: texto enhanced muito mais curto que o original. "
                "Mantendo markdown original."
            )
            return markdown_text

        # Check for remaining formula-not-decoded markers
        remaining = enhanced.count("<!-- formula-not-decoded -->")
        if remaining > 0:
            print(
                f"Aviso: {remaining} marcador(es) '<!-- formula-not-decoded -->' "
                f"ainda presentes após enhancement."
            )

        print("Enhancement concluído com sucesso")
        return enhanced

    except Exception as e:
        print(f"Erro no enhancement LLM ({e}). Retornando markdown original.")
        return markdown_text
