"""Markdown -> HTML -> PDF.

Por que HTML+Chromium e não python-docx/LaTeX? O CV fica com aparência profissional
controlada por CSS (templates/cv.css, fácil de ajustar), o Chromium gera PDF fiel, e os
ATS leem bem PDFs gerados assim (texto real, não imagem). Se o Playwright não estiver
instalado, salvamos .md e .html — o pipeline não para por causa disso.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import markdown as md

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:60]


def to_html(markdown_text: str, title: str) -> str:
    css = (TEMPLATES / "cv.css").read_text(encoding="utf-8")
    body = md.markdown(markdown_text, extensions=["tables", "sane_lists"])
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
                     margin={"top": "14mm", "bottom": "14mm", "left": "14mm", "right": "14mm"})
            b.close()
        return True
    except Exception as e:
        log.warning("falha ao gerar PDF (%s): mantendo .html", e)
        return False


def render_doc(markdown_text: str, out_dir: Path, basename: str) -> Path:
    """Salva .md, .html e (se possível) .pdf. Devolve o caminho do melhor formato gerado."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{basename}.md").write_text(markdown_text, encoding="utf-8")
    html = to_html(markdown_text, basename)
    html_path = out_dir / f"{basename}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = out_dir / f"{basename}.pdf"
    return pdf_path if html_to_pdf(html, pdf_path) else html_path
