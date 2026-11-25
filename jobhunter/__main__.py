"""CLI.

    python -m jobhunter run              # pipeline completo (o que o agendador chama todo dia)
    python -m jobhunter run --no-search  # só reprocessa vagas já no banco
    python -m jobhunter terms            # mostra/gera os termos de busca expandidos
    python -m jobhunter probe jobup.ch "chef de projet" Lausanne   # testa uma fonte
    python -m jobhunter approve          # revisa a fila: abre links/arquivos e marca status
    python -m jobhunter feedback 123 eu good   # calibração: essa vaga era boa/ruim para você
    python -m jobhunter report           # regenera o relatório do dia
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path

from .config import ROOT, load_candidates, load_settings


def load_dotenv(path: Path) -> None:
    """Lê KEY=valor do arquivo .env (sem sobrescrever variáveis já definidas no sistema).

    Assim não é preciso exportar a chave da API manualmente no terminal (útil no Windows).
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobhunter")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--no-search", action="store_true")
    sub.add_parser("terms")
    p = sub.add_parser("probe"); p.add_argument("source"); p.add_argument("query"); p.add_argument("location")
    p.add_argument("--country", default="CH")
    sub.add_parser("approve")
    f = sub.add_parser("feedback"); f.add_argument("job_id", type=int); f.add_argument("candidate")
    f.add_argument("verdict", choices=["good", "bad"])
    sub.add_parser("report")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Windows: o console/arquivo de log pode estar em cp1252; nunca deixar um caractere
    # como "→" ou "é" derrubar o programa.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    load_dotenv(ROOT / ".env")
    settings = load_settings()
    data = ROOT / settings.get("data_dir", "data")

    if args.cmd == "run":
        from .pipeline import Pipeline
        print(Pipeline(settings).run(skip_search=args.no_search))

    elif args.cmd == "terms":
        from .query_expansion import build_terms
        for c in load_candidates(settings):
            print(f"\n## {c.id}")
            for t in build_terms(c, settings["llm"]["expand"], data):
                print(" -", t)
        print(f"\nEdite/trave em {data}/search_terms_<id>.json (\"locked\": true) se quiser fixar a lista.")

    elif args.cmd == "probe":
        from .sources import build_sources
        srcs = [s for s in build_sources(settings) if s.name == args.source or args.source == "jobspy"]
        if not srcs:
            sys.exit(f"fonte {args.source} não está habilitada no settings.yaml")
        for s in srcs:
            jobs = s.search(args.query, args.location, args.country)
            print(f"{s.name}: {len(jobs)} vagas")
            for j in jobs[:10]:
                print(f"  - {j.title} | {j.company} | {j.location} | desc={len(j.description)} chars | {j.url}")

    elif args.cmd == "approve":
        from .db import DB
        db = DB(data / "jobhunter.db")
        rows = db.conn.execute("""SELECT a.id, a.candidate, a.status, a.method, a.cv_path, a.letter_path, a.notes,
                                  j.title, j.company, j.url, j.apply_url, j.apply_email
                                  FROM applications a JOIN jobs j ON j.id=a.job_id
                                  WHERE a.status IN ('drafted','needs_review') ORDER BY a.candidate, a.id""").fetchall()
        if not rows:
            print("Fila vazia.")
        for a in rows:
            print(f"\n[{a['id']}] {a['candidate']} — {a['title']} @ {a['company']} ({a['status']}, {a['method']})")
            if a["notes"]:
                print("   ⚠", a["notes"])
            print("   CV:", a["cv_path"], "\n   Carta:", a["letter_path"])
            print("   Link:", a["apply_url"] or a["url"], "| e-mail:", a["apply_email"] or "—")
            ch = input("   [o]abrir  [s]enviei  [x]descartar  [enter]pular > ").strip().lower()
            if ch == "o":
                for p in (a["cv_path"], a["letter_path"]):
                    webbrowser.open(Path(p).resolve().as_uri())
                webbrowser.open(a["apply_url"] or a["url"])
                ch = input("   [s]enviei  [x]descartar  [enter]depois > ").strip().lower()
            if ch == "s":
                db.set_application_status(a["id"], "submitted_manually")
            elif ch == "x":
                db.set_application_status(a["id"], "discarded")

    elif args.cmd == "feedback":
        from .db import DB
        db = DB(data / "jobhunter.db")
        db.conn.execute("UPDATE matches SET feedback=? WHERE job_id=? AND candidate=?",
                        (args.verdict, args.job_id, args.candidate))
        db.conn.commit()
        print("ok")

    elif args.cmd == "report":
        from . import report
        from .db import DB
        print(report.build(DB(data / "jobhunter.db"), data / "reports", since="2000-01-01"))


if __name__ == "__main__":
    main()
