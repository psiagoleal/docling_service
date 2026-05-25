# Caminho relativo: src/docling_service/app.py

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .docling_pipeline import process_files


def _zip_output_dir(output_dir: Path) -> io.BytesIO:
    """Compacta o diretório de saída (markdown + assets) em um zip em memória."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(output_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(output_dir))
    buf.seek(0)
    return buf


app = FastAPI(title="Docling Conversion Service")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "docling-service"}


@app.get("/", response_class=HTMLResponse)
async def web_ui():
    return """
    <!DOCTYPE html>
    <html><head><title>Docling Converter</title>
    <style>
      body { font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }
      .drop-zone { border: 2px dashed #ccc; padding: 40px; text-align: center; margin: 20px 0; border-radius: 8px; }
      .drop-zone.dragover { border-color: #4CAF50; background: #f0fff0; }
      button { background: #4CAF50; color: white; padding: 10px 24px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
      button:disabled { background: #ccc; }
      select { padding: 8px; border-radius: 4px; border: 1px solid #ccc; font-size: 14px; }
      pre { background: #f4f4f4; padding: 16px; border-radius: 4px; overflow-x: auto; max-height: 600px; white-space: pre-wrap; }
      .spinner { display: none; margin: 10px 0; }
      .spinner.active { display: block; }
    </style></head>
    <body>
      <h1>Docling Conversion Service</h1>
      <h3>Arquivo único</h3>
      <form id="single-form">
        <input type="file" id="single-file" accept=".pdf,.docx" required>
        <select id="single-format"><option value="json">Ver Markdown</option><option value="zip">Baixar ZIP</option></select>
        <label><input type="checkbox" id="single-enhance"> Enhance (LLM)</label>
        <label><input type="checkbox" id="single-remove-hf" checked> Remover cabeçalhos/rodapés (LLM)</label>
        <select id="single-text-provider"><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select>
        <select id="single-vision-provider"><option value="">Sem visão</option><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select>
        <button type="submit">Converter</button>
      </form>
      <h3>Múltiplos arquivos</h3>
      <form id="batch-form">
        <input type="file" id="batch-files" accept=".pdf,.docx" multiple required>
        <select id="batch-format"><option value="json">Ver Markdown</option><option value="zip">Baixar ZIP</option></select>
        <label><input type="checkbox" id="batch-enhance"> Enhance (LLM)</label>
        <label><input type="checkbox" id="batch-remove-hf" checked> Remover cabeçalhos/rodapés (LLM)</label>
        <select id="batch-text-provider"><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select>
        <select id="batch-vision-provider"><option value="">Sem visão</option><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select>
        <button type="submit">Converter lote</button>
      </form>
      <div class="spinner" id="spinner">Processando...</div>
      <div id="results"></div>
      <script>
        document.getElementById('single-form').onsubmit = async (e) => {
          e.preventDefault();
          const fd = new FormData();
          fd.append('file', document.getElementById('single-file').files[0]);
          const fmt = document.getElementById('single-format').value;
          const enh = document.getElementById('single-enhance').checked;
          const rhf = document.getElementById('single-remove-hf').checked;
          const tp = document.getElementById('single-text-provider').value;
          const vp = document.getElementById('single-vision-provider').value;
          let qs = '?format=' + fmt + '&enhance=' + enh + '&text_provider=' + tp;
          if (vp) qs += '&vision_provider=' + vp;
          if (rhf) qs += '&remove_headers_footers=true';
          document.getElementById('spinner').classList.add('active');
          if (fmt === 'zip') {
            const r = await fetch('/convert' + qs, { method: 'POST', body: fd });
            const blob = await r.blob();
            document.getElementById('spinner').classList.remove('active');
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = document.getElementById('single-file').files[0].name.replace(/\\.[^.]+$/, '') + '.zip';
            a.click();
            document.getElementById('results').innerHTML = '<p>Download iniciado.</p>';
          } else {
            const r = await fetch('/convert' + qs, { method: 'POST', body: fd });
            const data = await r.json();
            document.getElementById('spinner').classList.remove('active');
            document.getElementById('results').innerHTML = '<pre>' +
              (data.markdown || JSON.stringify(data, null, 2)) + '</pre>';
          }
        };
        document.getElementById('batch-form').onsubmit = async (e) => {
          e.preventDefault();
          const fd = new FormData();
          for (const f of document.getElementById('batch-files').files) fd.append('files', f);
          const bfmt = document.getElementById('batch-format').value;
          const benh = document.getElementById('batch-enhance').checked;
          const brhf = document.getElementById('batch-remove-hf').checked;
          const btp = document.getElementById('batch-text-provider').value;
          const bvp = document.getElementById('batch-vision-provider').value;
          let bqs = '?format=' + bfmt + '&enhance=' + benh + '&text_provider=' + btp;
          if (bvp) bqs += '&vision_provider=' + bvp;
          if (brhf) bqs += '&remove_headers_footers=true';
          document.getElementById('spinner').classList.add('active');
          if (bfmt === 'zip') {
            const r = await fetch('/convert/batch' + bqs, { method: 'POST', body: fd });
            const blob = await r.blob();
            document.getElementById('spinner').classList.remove('active');
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'batch_result.zip';
            a.click();
            document.getElementById('results').innerHTML = '<p>Download iniciado.</p>';
          } else {
            const r = await fetch('/convert/batch' + bqs, { method: 'POST', body: fd });
            const data = await r.json();
            document.getElementById('spinner').classList.remove('active');
            let html = '';
            for (const item of data.results || [data]) {
              html += '<h4>' + (item.filename || '') + '</h4><pre>' +
                (item.markdown || item.error || '') + '</pre>';
            }
            document.getElementById('results').innerHTML = html;
          }
        };
      </script>
    </body></html>
    """


@app.post("/convert")
async def convert_document(
    file: UploadFile = File(...),
    format: Literal["json", "zip"] = Query(
        "json", description="Formato da resposta: json ou zip"
    ),
    enhance: bool = Query(
        False, description="Pós-processar com LLM para corrigir equações e artefatos"
    ),
    text_provider: Literal["ollama", "openai", "anthropic"] = Query(
        "ollama", description="Provider do LLM de texto"
    ),
    vision_provider: Optional[Literal["ollama", "openai", "anthropic"]] = Query(
        None, description="Provider do modelo de visão (omitir para desativar)"
    ),
    remove_blocks: bool = Query(
        False, description="Remove blocos repetidos por contagem (regex, sem LLM)"
    ),
    remove_headers_footers: bool = Query(
        True,
        description=(
            "Remove cabeçalhos e rodapés identificados pelo modelo de visão (LLM). "
            "Requer enhance=true e um vision_provider configurado."
        ),
    ),
):
    with tempfile.TemporaryDirectory(prefix="docling_api_") as tmpdir:
        input_path = Path(tmpdir) / file.filename

        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        output_dir = process_files(
            str(input_path),
            generate_markdown=True,
            generate_latex=False,
            materialize_images=True,
            prefer_pdf_pipeline_docx=True,
            enhance=enhance,
            remove_repeated_blocks=remove_blocks,
            text_provider=text_provider,
            vision_provider=vision_provider,
            remove_headers_footers=remove_headers_footers,
        )

        try:
            md_files = list(Path(output_dir).glob("*.md"))
            if not md_files:
                return JSONResponse(
                    {"error": "Nenhum arquivo Markdown gerado"}, status_code=500
                )

            if format == "zip":
                zip_buf = _zip_output_dir(Path(output_dir))
                zip_name = Path(file.filename).stem + ".zip"
                return StreamingResponse(
                    zip_buf,
                    media_type="application/zip",
                    headers={
                        "Content-Disposition": f'attachment; filename="{zip_name}"'
                    },
                )

            md_path = md_files[0]
            md_content = md_path.read_text(encoding="utf-8")

            return {
                "markdown": md_content,
                "assets_dir": md_path.stem + "_assets",
            }
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


@app.post("/convert/batch")
async def convert_batch(
    files: List[UploadFile] = File(...),
    format: Literal["json", "zip"] = Query(
        "json", description="Formato da resposta: json ou zip"
    ),
    enhance: bool = Query(
        False, description="Pós-processar com LLM para corrigir equações e artefatos"
    ),
    text_provider: Literal["ollama", "openai", "anthropic"] = Query(
        "ollama", description="Provider do LLM de texto"
    ),
    vision_provider: Optional[Literal["ollama", "openai", "anthropic"]] = Query(
        None, description="Provider do modelo de visão (omitir para desativar)"
    ),
    remove_blocks: bool = Query(
        False, description="Remove blocos repetidos por contagem (regex, sem LLM)"
    ),
    remove_headers_footers: bool = Query(
        True,
        description=(
            "Remove cabeçalhos e rodapés identificados pelo modelo de visão (LLM). "
            "Requer enhance=true e um vision_provider configurado."
        ),
    ),
):
    all_output_dirs = []
    results = []
    for file in files:
        with tempfile.TemporaryDirectory(prefix="docling_api_") as tmpdir:
            input_path = Path(tmpdir) / file.filename

            with open(input_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            try:
                output_dir = process_files(
                    str(input_path),
                    generate_markdown=True,
                    generate_latex=False,
                    materialize_images=True,
                    prefer_pdf_pipeline_docx=True,
                    enhance=enhance,
                    remove_repeated_blocks=remove_blocks,
                    text_provider=text_provider,
                    vision_provider=vision_provider,
                    remove_headers_footers=remove_headers_footers,
                )
                all_output_dirs.append((file.filename, Path(output_dir)))
                md_files = list(Path(output_dir).glob("*.md"))
                if not md_files:
                    results.append(
                        {"filename": file.filename, "error": "Nenhum Markdown gerado"}
                    )
                    continue

                md_content = md_files[0].read_text(encoding="utf-8")
                results.append(
                    {
                        "filename": file.filename,
                        "markdown": md_content,
                    }
                )
            except Exception as e:
                results.append({"filename": file.filename, "error": str(e)})

    try:
        if format == "zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for orig_name, out_dir in all_output_dirs:
                    prefix = Path(orig_name).stem
                    for file_path in sorted(out_dir.rglob("*")):
                        if file_path.is_file():
                            zf.write(
                                file_path, Path(prefix) / file_path.relative_to(out_dir)
                            )
            buf.seek(0)
            return StreamingResponse(
                buf,
                media_type="application/zip",
                headers={
                    "Content-Disposition": 'attachment; filename="batch_result.zip"'
                },
            )

        return {"results": results}
    finally:
        for _, out_dir in all_output_dirs:
            shutil.rmtree(out_dir, ignore_errors=True)
