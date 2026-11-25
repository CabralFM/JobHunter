"""Relatório diário: data/reports/AAAA-MM-DD.html + .csv (abre no Excel)."""

from __future__ import annotations

import csv
import html
import json
from datetime import date
from pathlib import Path

from .db import DB

QUERY = """
SELECT m.candidate, m.decision, m.priority, m.score_current, m.score_potential, m.desire_fit,
       m.assessment_json, m.created_at, j.id AS job_id, j.title, j.company, j.location, j.country,
       j.url, j.apply_url, j.apply_email, j.source, j.also_seen_on, j.language, j.workload,
       a.status AS app_status, a.cv_path, a.letter_path
FROM matches m JOIN jobs j ON j.id = m.job_id
LEFT JOIN applications a ON a.job_id = m.job_id AND a.candidate = m.candidate
WHERE m.decision IN ('apply','review') AND date(m.created_at) >= date(?)
ORDER BY m.candidate, m.priority DESC
"""

BADGE = {"apply": "#1b7f3b", "review": "#b26b00"}


def build(db: DB, out_dir: Path, since: str | None = None) -> Path:
    since = since or date.today().isoformat()
    rows = db.conn.execute(QUERY, (since,)).fetchall()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / date.today().isoformat()

    with open(f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["candidato", "decisao", "prioridade", "match_cv_atual", "match_potencial", "desejo",
                    "titulo", "empresa", "local", "pais", "idioma", "taxa", "link", "link_candidatura",
                    "email", "status_candidatura", "cv", "carta", "resumo"])
        for r in rows:
            a = json.loads(r["assessment_json"] or "{}")
            w.writerow([r["candidate"], r["decision"], r["priority"], r["score_current"], r["score_potential"],
                        r["desire_fit"], r["title"], r["company"], r["location"], r["country"], r["language"],
                        r["workload"], r["url"], r["apply_url"], r["apply_email"], r["app_status"],
                        r["cv_path"], r["letter_path"], a.get("summary", "")])

    e = html.escape
    cards = []
    for r in rows:
        a = json.loads(r["assessment_json"] or "{}")
        gain = (r["score_potential"] or 0) - (r["score_current"] or 0)
        files = "".join(f"<a href='{e(Path(p).resolve().as_uri())}'>{lbl}</a> "
                        for p, lbl in ((r["cv_path"], "CV"), (r["letter_path"], "Carta")) if p)
        cards.append(f"""
<tr><td><b>{e(r['candidate'])}</b></td>
<td><span style="background:{BADGE.get(r['decision'], '#555')};color:#fff;padding:1px 6px;border-radius:4px">{r['decision']}</span></td>
<td><b>{r['priority']}</b></td><td>{r['score_current']} → {r['score_potential']} <small>(+{gain:.0f})</small></td>
<td>{r['desire_fit']}</td>
<td><a href="{e(r['url'] or '')}">{e(r['title'])}</a><br><small>{e(r['company'])} · {e(r['location'])} · {e(r['language'] or '')} · {e(r['source'])}</small>
<details><summary>análise</summary><p>{e(a.get('summary', ''))}</p>
<p><b>Resolve com CV:</b> {e(', '.join(a.get('gaps_fixable_by_tailoring', [])))}</p>
<p><b>Lacunas reais:</b> {e(', '.join(a.get('real_gaps', [])))}</p></details></td>
<td>{e(r['app_status'] or '—')}<br>{files}</td></tr>""")

    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Vagas {date.today()}</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;color:#222}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}}small{{color:#666}}</style></head>
<body><h1>Vagas — {date.today()}</h1>
<p>{len(rows)} vagas com decisão <i>apply</i> ou <i>review</i>. Coluna "match": CV atual → potencial (após adaptação).</p>
<table><tr><th>Quem</th><th>Decisão</th><th>Prioridade</th><th>Match</th><th>Desejo</th><th>Vaga</th><th>Candidatura</th></tr>
{''.join(cards)}</table></body></html>"""
    Path(f"{stem}.html").write_text(page, encoding="utf-8")
    return Path(f"{stem}.html")
