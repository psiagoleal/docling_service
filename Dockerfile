FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04 AS base

ARG DEBIAN_FRONTEND=noninteractive

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Instalar Python 3.12 e dependências de runtime do serviço.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
        libreoffice \
        pandoc \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && python -m ensurepip --upgrade \
    && python -m pip install --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS builder

# Dependências de build necessárias apenas para empacotar a aplicação local.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        python3.12-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

FROM base AS runtime

COPY requirements.txt ./

# Instala dependências de runtime (incluindo wheels CUDA do PyTorch).
RUN python -m pip install --no-cache-dir -r requirements.txt

# Instala o pacote da aplicação a partir do wheel gerado no estágio de build.
COPY --from=builder /dist/*.whl /tmp/dist/
RUN python -m pip install --no-cache-dir /tmp/dist/*.whl \
    && rm -rf /tmp/dist

# Executa o serviço com usuário não-root.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "docling_service.app:app", "--host", "0.0.0.0", "--port", "8001"]
