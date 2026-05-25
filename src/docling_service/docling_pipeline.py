# Caminho relativo: src/docling_service/docling_pipeline.py

import base64
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Literal, Optional, Tuple

from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode


def _sanitize_docx_math(input_path: Path) -> Path:
    """
    Cria uma cópia do DOCX substituindo blocos OMML (<m:oMath>, <m:oMathPara>)
    por um run de texto [EQ], evitando falhas do parser de equações do docling.
    """
    input_path = Path(input_path)
    tmpdir = tempfile.mkdtemp(prefix="docx_sanitized_")
    sanitized_path = Path(tmpdir) / f"{input_path.stem}_sanitized.docx"

    with (
        zipfile.ZipFile(input_path, "r") as zin,
        zipfile.ZipFile(sanitized_path, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            data = zin.read(item.filename)
            # Processa apenas XMLs dentro de "word/"
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                try:
                    txt = data.decode("utf-8")
                except UnicodeDecodeError:
                    # Se não for UTF-8, só copia
                    zout.writestr(item, data)
                    continue

                # Substitui blocos OMML por um run de texto simples
                def _extract_omml_text(omml_block: str) -> str:
                    texts = re.findall(r"<m:t[^>]*>(.*?)</m:t>", omml_block)
                    return " ".join(texts) if texts else "[EQ]"

                txt = re.sub(
                    r"<m:oMathPara[^>]*>(.*?)</m:oMathPara>",
                    lambda m: f"<w:r><w:t>{_extract_omml_text(m.group(1))}</w:t></w:r>",
                    txt,
                    flags=re.DOTALL,
                )
                txt = re.sub(
                    r"<m:oMath[^>]*>(.*?)</m:oMath>",
                    lambda m: f"<w:r><w:t>{_extract_omml_text(m.group(1))}</w:t></w:r>",
                    txt,
                    flags=re.DOTALL,
                )
                zout.writestr(item, txt.encode("utf-8"))
            else:
                zout.writestr(item, data)

    return sanitized_path


def _convert_with_fallback(converter: DocumentConverter, input_path: Path):
    """
    Tenta converter; se falhar por erro no parser de equação, sanitiza DOCX e tenta de novo.
    """
    try:
        return converter.convert(input_path)
    except Exception as e:
        # Só tenta fallback para DOCX
        if input_path.suffix.lower() == ".docx":
            print(
                f"Aviso: falha ao converter DOCX ({e.__class__.__name__}). Tentando sanitizar equações…"
            )
            sanitized = _sanitize_docx_math(input_path)
            return converter.convert(sanitized)
        raise


def _find_soffice() -> Optional[str]:
    """
    Tenta localizar o LibreOffice tanto no Linux/WSL quanto no Windows (via WSL).
    """
    # 1) Linux/WSL PATH
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    # 2) Windows (executável acessível via WSL)
    win_candidates = [
        "/mnt/c/Program Files/LibreOffice/program/soffice.exe",
        "/mnt/c/Program Files (x86)/LibreOffice/program/soffice.exe",
    ]
    for p in win_candidates:
        if os.path.exists(p):
            return p
    return None


def _to_windows_path(p: Path) -> str:
    """
    Converte caminho WSL -> Windows (C:\\...) para passar a um .exe do Windows.
    Requer 'wslpath' disponível.
    """
    try:
        r = subprocess.run(
            ["wslpath", "-w", str(p)], check=True, capture_output=True, text=True
        )
        return r.stdout.strip()
    except Exception:
        # fallback simples: /mnt/c/... -> C:\...
        s = str(p)
        if s.startswith("/mnt/"):
            drive = s[5].upper()
            rest = s[7:]
            return f"{drive}:\\" + rest.replace("/", "\\")
        return s


def _docx_to_pdf(input_docx: Path) -> Path:
    """
    Converte DOCX -> PDF via LibreOffice (Linux ou Windows).
    - Se usar soffice.exe do Windows, grava o PDF ao lado do DOCX (em /mnt/c/...).
    - Caso Linux, usa diretório temporário.
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) não encontrado no PATH para fallback DOCX->PDF."
        )

    is_windows_soffice = soffice.lower().endswith(".exe")
    if is_windows_soffice:
        # Windows exe exige caminhos em formato Windows e não escreve em /tmp do WSL.
        outdir = input_docx.parent
        in_win = _to_windows_path(input_docx)
        out_win = _to_windows_path(outdir)
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            out_win,
            in_win,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pdf_path = outdir / (input_docx.stem + ".pdf")
    else:
        outdir = Path(tempfile.mkdtemp(prefix="docx2pdf_"))
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(input_docx),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pdf_path = outdir / (input_docx.stem + ".pdf")

    if not pdf_path.exists():
        raise RuntimeError("Falha ao gerar PDF via LibreOffice.")
    return pdf_path


def _convert_docx_via_pdf_pipeline(input_docx: Path):
    """
    Usa rota alternativa: DOCX -> PDF (LibreOffice) -> docling(PDF).
    """
    pdf_path = _docx_to_pdf(input_docx)
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.images_scale = 2.0  # default é 1.0; 2.0 dobra a resolução
    pipeline_options.do_ocr = True  # OCR para páginas escaneadas
    pipeline_options.ocr_options = EasyOcrOptions(lang=["pt", "en"])  # idiomas
    pipeline_options.do_table_structure = True  # detecção de estrutura de tabelas
    pipeline_options.table_structure_options.mode = (
        TableFormerMode.ACCURATE
    )  # modo mais preciso
    converter = DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
    )
    return converter.convert(pdf_path)


def _materialize_data_uri_images(
    markdown_text: str, assets_dir: Path
) -> Tuple[str, int]:
    """
    Salva imagens em data URI (data:image/*;base64,...) em arquivos e reescreve os links.
    Retorna (markdown_reescrito, num_imagens_salvas).
    """
    assets_dir.mkdir(parents=True, exist_ok=True)
    # ![alt](data:image/png;base64,AAAA...)  – pega mime e payload
    pattern = re.compile(
        r"!\[([^\]]*)\]\((data:image/([a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+))\)"
    )
    count = 0

    def _ext_for_mime(m: str) -> str:
        # svg+xml -> svg; jpeg -> jpg
        if m.startswith("svg"):
            return "svg"
        if m == "jpeg":
            return "jpg"
        return m

    def repl(m: re.Match) -> str:
        nonlocal count
        alt = m.group(1)
        mime = m.group(3)  # e.g. png, jpeg, svg+xml
        b64 = m.group(4)
        ext = _ext_for_mime(mime)
        data = base64.b64decode(re.sub(r"\s+", "", b64))
        fname = f"img_{count:04d}.{ext}"
        out_path = assets_dir / fname
        with open(out_path, "wb") as f:
            f.write(data)
        count += 1
        # reescreve para caminho relativo
        rel = os.path.join(assets_dir.name, fname)
        return f"![{alt}]({rel})"

    new_md = pattern.sub(repl, markdown_text)
    return new_md, count


def _normalize_math_unicode(text: str) -> str:
    """
    Normaliza caracteres 'Mathematical Alphanumeric Symbols' para ASCII (ex.: 𝑓→f),
    reduzindo artefatos como '𝑓   𝑠𝑙' no Markdown.
    """
    try:
        return unicodedata.normalize("NFKC", text)
    except Exception:
        return text


def _remove_repeated_blocks(markdown_text: str, min_repetitions: int = 3) -> str:
    """
    Remove blocos de texto que se repetem múltiplas vezes no documento,
    típicos de cabeçalhos e rodapés de página.
    Blocos com menos de 300 caracteres que aparecem >= min_repetitions vezes são removidos.
    Headings Markdown (# ...) são preservados.
    """
    from collections import Counter

    blocks = re.split(r"\n{2,}", markdown_text)
    normalized = [b.strip() for b in blocks]
    counts = Counter(normalized)

    repeated = {
        text
        for text, count in counts.items()
        if count >= min_repetitions
        and 0 < len(text) < 300
        and not re.match(r"^#{1,6}\s", text)
    }

    if repeated:
        print(
            f"Pós-processamento: removidos {len(repeated)} padrão(ões) de cabeçalho/rodapé repetido(s)"
        )

    filtered = [b for b in blocks if b.strip() not in repeated]
    result = "\n\n".join(filtered)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def _convert_docx_with_pandoc(input_path: Path, assets_dir: Path) -> str:
    """
    Converte DOCX → Markdown via pandoc, preservando equações OMML como LaTeX ($...$).
    Imagens são extraídas para assets_dir.
    """
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("Pandoc não encontrado no PATH.")

    assets_dir.mkdir(parents=True, exist_ok=True)
    cwd = assets_dir.parent
    media_rel = assets_dir.name

    cmd = [
        pandoc,
        str(input_path.resolve()),
        "-f",
        "docx",
        "-t",
        "markdown+tex_math_dollars",
        "--wrap=none",
        f"--extract-media={media_rel}",
    ]
    result = subprocess.run(
        cmd, check=True, capture_output=True, text=True, cwd=str(cwd)
    )
    return result.stdout


def export_to_markdown(
    input_path: str,
    output_path: str,
    *,
    materialize_images: bool = True,
    prefer_pdf_pipeline_docx: bool = False,
    use_pandoc_for_docx: bool = True,
    normalize_unicode: bool = False,
    remove_repeated_blocks: bool = True,
    enhance: bool = False,
    text_provider: Literal["ollama", "openai", "anthropic"] = "ollama",
    text_model: Optional[str] = None,
    vision_provider: Optional[Literal["ollama", "openai", "anthropic"]] = None,
    vision_model: Optional[str] = None,
    remove_headers_footers: bool = False,
) -> Tuple[Path, Optional[Path]]:
    """
    Converte para Markdown. Se materialize_images=True, salva imagens em disco (em <basename>_assets).
    Se use_pandoc_for_docx=True, usa pandoc para DOCX (preserva equações como LaTeX).
    Se prefer_pdf_pipeline_docx=True (e pandoc falhar/desabilitado), usa rota DOCX->PDF->docling.
    Se remove_repeated_blocks=True, remove cabeçalhos/rodapés repetidos.
    Se enhance=True, usa LLM para pós-processar e corrigir o Markdown.
    Retorna (caminho_md, pasta_assets_ou_None).
    """
    input_path = str(input_path)
    ext = Path(input_path).suffix.lower()
    assets_dir: Optional[Path] = None
    markdown_text = None

    # --- DOCX via Pandoc (preserva equações OMML como LaTeX) ---
    if ext == ".docx" and use_pandoc_for_docx:
        try:
            assets_dir = (
                Path(output_path)
                .with_suffix("")
                .with_name(Path(output_path).stem + "_assets")
            )
            markdown_text = _convert_docx_with_pandoc(Path(input_path), assets_dir)
            print("DOCX convertido via Pandoc (equações preservadas como LaTeX)")
        except Exception as e:
            print(f"Aviso: Pandoc falhou ({e}). Usando pipeline Docling…")
            markdown_text = None

    # --- Pipeline Docling (PDF, ou DOCX se pandoc falhou/desabilitado) ---
    if markdown_text is None:
        if ext == ".pdf":
            pipeline_options = PdfPipelineOptions()
            pipeline_options.generate_page_images = True
            pipeline_options.generate_picture_images = True
            pipeline_options.images_scale = 2.0
            pipeline_options.do_ocr = True
            pipeline_options.ocr_options = EasyOcrOptions(lang=["pt", "en"])
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
            converter = DocumentConverter(
                format_options={
                    "pdf": PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            result = _convert_with_fallback(converter, Path(input_path))
        elif ext == ".docx":
            if prefer_pdf_pipeline_docx:
                try:
                    result = _convert_docx_via_pdf_pipeline(Path(input_path))
                except RuntimeError as e:
                    print(f"Aviso: {e}. Fazendo fallback para pipeline DOCX nativo…")
                    converter = DocumentConverter()
                    result = _convert_with_fallback(converter, Path(input_path))
            else:
                converter = DocumentConverter()
                result = _convert_with_fallback(converter, Path(input_path))
        else:
            raise ValueError(f"Formato não suportado: {ext}")

        markdown_text = result.document.export_to_markdown(
            image_mode=ImageRefMode.EMBEDDED
        )

        if normalize_unicode:
            markdown_text = _normalize_math_unicode(markdown_text)

        if materialize_images:
            assets_dir = (
                Path(output_path)
                .with_suffix("")
                .with_name(Path(output_path).stem + "_assets")
            )
            markdown_text, _ = _materialize_data_uri_images(markdown_text, assets_dir)

    # --- Pós-processamento: remover cabeçalhos/rodapés repetidos ---
    if remove_repeated_blocks:
        markdown_text = _remove_repeated_blocks(markdown_text)

    # --- Enhancement via LLM (opcional) ---
    if enhance:
        from .llm_enhance import enhance_markdown

        markdown_text = enhance_markdown(
            markdown_text,
            original_path=input_path,
            text_provider=text_provider,
            text_model=text_model,
            vision_provider=vision_provider,
            vision_model=vision_model,
            remove_headers_footers=remove_headers_footers,
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    print(f"Markdown salvo em: {output_path}")
    return Path(output_path), assets_dir


def export_to_latex(
    input_path: str,
    output_path: str,
    *,
    prefer_pdf_pipeline_docx: bool = False,
):
    """
    Gera LaTeX via Pandoc a partir do Markdown (docling não oferece export_to_latex).
    Reexecuta a conversão para MD em um arquivo temporário e materializa imagens.
    Requer pandoc no PATH.
    """
    # 1) Converter para MD temporário
    tmp_md = Path(tempfile.mkdtemp(prefix="md4tex_")) / "doc.md"
    md_path, assets_dir = export_to_markdown(
        input_path,
        str(tmp_md),
        materialize_images=True,
        prefer_pdf_pipeline_docx=prefer_pdf_pipeline_docx,
    )

    # 2) Chamar pandoc
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError(
            "Pandoc não encontrado no PATH. Instale o pandoc para gerar LaTeX."
        )

    # CWD no diretório de saída para que caminhos relativos de imagem funcionem
    cmd = [
        pandoc,
        str(md_path.name),
        "--from",
        "gfm",
        "--to",
        "latex",
        "--standalone",
        "-o",
        str(Path(output_path).name),
    ]
    # Se houver assets, garantir que o pandoc enxergue
    env = os.environ.copy()
    # Executa no dir onde estão md e assets
    cwd = md_path.parent
    subprocess.run(cmd, check=True, cwd=cwd, env=env)

    # Mover resultado para o caminho solicitado, se ainda não estiver lá
    produced = cwd / Path(output_path).name
    if produced.resolve() != Path(output_path).resolve():
        shutil.move(str(produced), str(output_path))

    print(f"LaTeX salvo em: {output_path}")


def _create_output_dir() -> Path:
    """
    Cria uma pasta de saída com timestamp no diretório atual.
    Formato: docling_output_YYYYMMDDHHMMSS
    """
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_dir = Path.cwd() / f"docling_output_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _get_supported_files(input_path: Path) -> list[Path]:
    """
    Retorna lista de arquivos suportados (.pdf, .docx).
    Se input_path for um arquivo, retorna lista com ele.
    Se for uma pasta, retorna todos os arquivos suportados dentro dela.
    """
    supported_extensions = {".pdf", ".docx"}

    if input_path.is_file():
        if input_path.suffix.lower() in supported_extensions:
            return [input_path]
        else:
            raise ValueError(f"Formato não suportado: {input_path.suffix}")

    elif input_path.is_dir():
        files = []
        for ext in supported_extensions:
            files.extend(input_path.glob(f"*{ext}"))
            files.extend(input_path.glob(f"*{ext.upper()}"))
        if not files:
            raise ValueError(
                f"Nenhum arquivo .pdf ou .docx encontrado em: {input_path}"
            )
        return sorted(set(files))  # Remove duplicatas e ordena

    else:
        raise ValueError(f"Caminho não encontrado: {input_path}")


def process_files(
    input_path: str | Path,
    *,
    generate_markdown: bool = True,
    generate_latex: bool = False,
    materialize_images: bool = True,
    prefer_pdf_pipeline_docx: bool = False,
    use_pandoc_for_docx: bool = True,
    remove_repeated_blocks: bool = True,
    enhance: bool = False,
    text_provider: Literal["ollama", "openai", "anthropic"] = "ollama",
    text_model: Optional[str] = None,
    vision_provider: Optional[Literal["ollama", "openai", "anthropic"]] = None,
    vision_model: Optional[str] = None,
    remove_headers_footers: bool = False,
) -> Path:
    """
    Processa um arquivo ou todos os arquivos .pdf/.docx de uma pasta.

    Args:
        input_path: Caminho para arquivo ou pasta.
        generate_markdown: Se True, gera arquivos .md.
        generate_latex: Se True, gera arquivos .tex via Pandoc.
        materialize_images: Se True, salva imagens em disco.
        prefer_pdf_pipeline_docx: Se True, converte DOCX via PDF (LibreOffice).
        use_pandoc_for_docx: Se True, usa pandoc para DOCX (preserva equações).
        remove_repeated_blocks: Se True, remove cabeçalhos/rodapés repetidos.
        enhance: Se True, usa LLM para pós-processar.
        text_provider: Provider do LLM de texto (ollama, openai, anthropic).
        text_model: Modelo de texto (override).
        vision_provider: Provider do modelo de visão (None para desativar).
        vision_model: Modelo de visão (override).

    Returns:
        Path da pasta de saída criada.
    """
    input_path = Path(input_path)
    files = _get_supported_files(input_path)
    output_dir = _create_output_dir()

    print(f"Pasta de saída: {output_dir}")
    print(f"Arquivos a processar: {len(files)}")
    print("-" * 50)

    for i, file_path in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] Processando: {file_path.name}")

        # Define nomes de saída baseados no nome do arquivo original
        base_name = file_path.stem
        md_output = output_dir / f"{base_name}.md"
        tex_output = output_dir / f"{base_name}.tex"

        try:
            if generate_markdown:
                export_to_markdown(
                    str(file_path),
                    str(md_output),
                    materialize_images=materialize_images,
                    prefer_pdf_pipeline_docx=prefer_pdf_pipeline_docx,
                    use_pandoc_for_docx=use_pandoc_for_docx,
                    remove_repeated_blocks=remove_repeated_blocks,
                    enhance=enhance,
                    text_provider=text_provider,
                    text_model=text_model,
                    vision_provider=vision_provider,
                    vision_model=vision_model,
                    remove_headers_footers=remove_headers_footers,
                )

            if generate_latex:
                export_to_latex(
                    str(file_path),
                    str(tex_output),
                    prefer_pdf_pipeline_docx=prefer_pdf_pipeline_docx,
                )

        except Exception as e:
            print(f"  ERRO ao processar {file_path.name}: {e}")
            continue

    print("-" * 50)
    print(f"Processamento concluído. Saída em: {output_dir}")
    return output_dir


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Converte um arquivo PDF/DOCX ou um diretório usando o pipeline Docling."
        )
    )
    parser.add_argument(
        "input_path",
        help="Caminho para um arquivo PDF/DOCX ou diretório contendo documentos.",
    )
    args = parser.parse_args()

    input_path = args.input_path
    if not Path(input_path).exists():
        parser.error(f"caminho não encontrado: {input_path}")

    process_files(
        input_path,
        generate_markdown=True,
        generate_latex=False,
        materialize_images=True,
        prefer_pdf_pipeline_docx=True,
    )


if __name__ == "__main__":
    main()
