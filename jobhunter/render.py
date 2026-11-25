"""Markdown -> HTML -> PDF.

Por que HTML+Chromium e não python-docx/LaTeX? O CV fica com aparência profissional
controlada por CSS (templates/cv.css, fácil de ajustar), o Chromium gera PDF fiel, e os
ATS leem bem PDFs gerados assim (texto real, não imagem). Se o Playwright não estiver
instalado, salvamos .md e .html — o pipeline não para por causa disso.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from pathlib import Path

import markdown as md

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:60]


def to_html(markdown_text: str, title: str, photo: Path | None = None) -> str:
    css = (TEMPLATES / "cv.css").read_text(encoding="utf-8")
    # Markdown "estrito" exige linha em branco antes de lista; LLMs e humanos costumam omitir,
    # e a lista vira parágrafo corrido. Inserimos a linha em branco que falta.
    markdown_text = re.sub(r"(?m)^(?![ \t]*[-*+] |[ \t]*\d+\. |\s*$)(.+)\n(?=[ \t]*([-*+]|\d+\.) )",
                           r"\1\n\n", markdown_text)
    # nl2br: quebras de linha simples (ex.: cabeçalho com telefone/e-mail) são respeitadas
    body = md.markdown(markdown_text, extensions=["tables", "sane_lists", "nl2br"])
    if photo and Path(photo).exists():
        # foto embutida (base64) para o PDF não depender de arquivo externo; vai à direita do nome
        mime = mimetypes.guess_type(str(photo))[0] or "image/jpeg"
        b64 = base64.b64encode(Path(photo).read_bytes()).decode()
        body = f"<img class='photo' alt='' src='data:{mime};base64,{b64}'>" + body
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>" \
           f"<style>{css}</style></head><body>{body}</body></html>"


def html_to_pdf(html: str, out: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("playwright não instalado: gerando só .html")
        return False
    try:
        with sync_playwright() as p:
            # CHROMIUM_PATH permite usar um Chrome/Chromium já instalado em vez do baixado
            exe = os.environ.get("CHROMIUM_PATH")
            b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            page = b.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), format="A4", print_background=True,
                     margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
            b.close()
        return True
    except Exception as e:
        log.warning("falha ao gerar PDF (%s): mantendo .html", e)
        return False


def render_doc(markdown_text: str, out_dir: Path, basename: str, photo: Path | None = None) -> Path:
    """Salva .md, .html e (se possível) .pdf. Devolve o caminho do melhor formato gerado."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{basename}.md").write_text(markdown_text, encoding="utf-8")
    html = to_html(markdown_text, basename, photo)
    html_path = out_dir / f"{basename}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = out_dir / f"{basename}.pdf"
    try:
        if pdf_path.exists():
            pdf_path.open("ab").close()  # falha se o PDF estiver aberto no leitor (Windows)
    except PermissionError:
        from datetime import datetime
        pdf_path = out_dir / f"{basename}-{datetime.now():%H%M%S}.pdf"
        log.warning("PDF anterior está aberto em outro programa; gravando como %s", pdf_path.name)
    return pdf_path if html_to_pdf(html, pdf_path) else html_path
