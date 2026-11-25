"""Match vaga × candidato em duas etapas, com DUAS notas.

Etapa 1 — triagem (1 chamada por vaga, avalia os 2 candidatos de uma vez, effort baixo):
    descarta o óbvio (área errada, estágio, alemão nativo obrigatório...) e extrai idioma da
    vaga e requisitos eliminatórios. Costuma eliminar 60–80% do volume.

Etapa 2 — avaliação detalhada (1 chamada por vaga × candidato relevante):
    o Claude dá notas POR DIMENSÃO com evidência; a nota final é calculada AQUI, em código,
    com pesos fixos. Motivo: pedir "dê uma nota de 0 a 100" direto ao LLM gera números
    instáveis entre execuções; notas por dimensão com evidência + pesos determinísticos são
    bem mais estáveis e auditáveis (você vê de onde veio cada ponto).

As duas notas — a ideia central para o seu caso:
    score_current   = aderência do CV ATUAL (que está ajustado para outras vagas)
    score_potential = aderência possível usando o PERFIL MESTRE (tudo o que é verdade sobre você)
    A diferença entre elas é exatamente o "ganho de adaptação": vagas com current 45 e
    potential 80 são as que mais se beneficiam de um CV reescrito — e são as que um filtro
    simples por palavra-chave jogaria fora.

E uma terceira: desire_fit = quanto a vaga corresponde ao que VOCÊ quer (não só ao que você sabe
fazer). Uma vaga em que você "dá match" mas que é um passo atrás na carreira não deve ser
aplicada automaticamente.
"""

from __future__ import annotations

import json

from .config import Candidate
from .llm import ask
from .models import MatchAssessment, Triage

DEFAULT_WEIGHTS = {
    "hard_skills": 0.30,
    "experience": 0.20,
    "domain": 0.15,
    "languages": 0.15,
    "location_permit": 0.10,
    "education": 0.10,
}

TRIAGE_SYSTEM = """Você faz a triagem inicial de vagas de emprego (Suíça e Luxemburgo) para uma
família de candidatos. Seja inclusivo: marque relevant=true sempre que houver chance REAL de o
candidato ser competitivo, mesmo que o título da vaga seja diferente dos cargos-alvo — o que
importa é o conteúdo do trabalho. Marque false quando a área for outra, o nível for muito
diferente, ou houver um requisito eliminatório claro que o candidato não cumpre.

Candidatos:
{candidates}"""

ASSESS_SYSTEM = """Você é um recrutador experiente na Suíça romanda e em Luxemburgo avaliando a
aderência de UM candidato a UMA vaga. Seja rigoroso e honesto — notas infladas fazem o candidato
perder tempo e queimar contatos com recrutadores.

Para cada dimensão dê duas notas 0–100:
- score_current_cv: usando APENAS o CV atual, como um recrutador/ATS o leria hoje.
- score_potential: usando o perfil mestre — o que seria possível mostrar num CV reescrito,
  SEM inventar nada. Se o fato não está no perfil mestre, ele não existe.

Dimensões: hard_skills, experience (anos/nível/escopo), domain (setor/indústria),
languages (níveis exigidos vs. reais), location_permit (local, permis de travail, remoto,
fronteiriço), education (diplomas/certificações).

desire_fit: compare a vaga com as preferências declaradas (cargos desejados, setores, salário,
taxa de ocupação, tipo de empresa, o que NÃO quer). 100 = é exatamente o que procura.

knockouts: só requisitos explicitamente obrigatórios que o candidato comprovadamente não cumpre.

## Candidato: {name}

### O que procura (profile.yaml)
{profile}

### Perfil mestre (fonte da verdade)
{master}

### CV atual
{current}"""


def candidate_brief(c: Candidate) -> str:
    p = c.profile
    return (f"- id={c.id}: alvo={p.get('target_roles')}; anos_exp={p.get('years_experience')}; "
            f"idiomas={p.get('languages')}; nao_quer={p.get('dealbreakers')}; "
            f"resumo={p.get('summary', '')[:500]}")


def triage(job_row, candidates: list[Candidate], step_cfg: dict) -> Triage:
    system = TRIAGE_SYSTEM.format(candidates="\n".join(candidate_brief(c) for c in candidates))
    return ask(
        system=system,
        user=f"Vaga: {job_row['title']} — {job_row['company']} — {job_row['location']}\n\n"
             f"{(job_row['description'] or '')[:12000]}",
        schema=Triage,
        step_cfg=step_cfg,
        max_tokens=3000,
    )


def assess(job_row, cand: Candidate, step_cfg: dict) -> MatchAssessment:
    system = ASSESS_SYSTEM.format(
        name=cand.name,
        profile=json.dumps(cand.profile, ensure_ascii=False, indent=2),
        master=cand.master_cv,
        current=cand.current_cv,
    )
    return ask(
        system=system,
        user=f"# Vaga\nTítulo: {job_row['title']}\nEmpresa: {job_row['company']}\n"
             f"Local: {job_row['location']} ({job_row['country']})\nTaxa: {job_row['workload'] or '?'}\n\n"
             f"{(job_row['description'] or '')[:20000]}",
        schema=MatchAssessment,
        step_cfg=step_cfg,
        max_tokens=8000,
    )


def score(a: MatchAssessment, weights: dict | None = None) -> tuple[float, float]:
    w = weights or DEFAULT_WEIGHTS
    total = sum(w.get(d.name, 0) for d in a.dimensions) or 1
    cur = sum(w.get(d.name, 0) * d.score_current_cv for d in a.dimensions) / total
    pot = sum(w.get(d.name, 0) * d.score_potential for d in a.dimensions) / total
    return round(cur, 1), round(pot, 1)


def decide(current: float, potential: float, desire: float, knockouts: list[str], th: dict) -> tuple[str, float]:
    """Regra de decisão. Limiares no settings.yaml (seção thresholds).

    priority mistura aderência possível (70%) e desejo (30%): é o que ordena a lista diária.
    """
    priority = round(0.7 * potential + 0.3 * desire, 1)
    if knockouts:
        return "knockout", priority
    if potential >= th["apply_min_potential"] and desire >= th["apply_min_desire"]:
        return "apply", priority
    if potential >= th["review_min_potential"]:
        return "review", priority
    return "skip", priority
