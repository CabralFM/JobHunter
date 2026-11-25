"""Geração do CV adaptado + carta de motivação, com verificação anti-invenção.

Princípio: o CV adaptado é uma RE-SELEÇÃO e RE-REDAÇÃO do perfil mestre, nunca uma ficção.
Um CV com uma competência inventada pode passar no ATS, mas explode na entrevista — e, no
mercado pequeno da Suíça romanda/Luxemburgo, os mesmos recrutadores (Michael Page, Hays,
Adecco, agências internas de bancos) veem dezenas de vagas. Reputação conta.

Por isso há duas chamadas:
1. `tailor`: escreve CV + carta no idioma da vaga, espelhando o vocabulário do anúncio.
2. `verify`: um "auditor" independente compara cada afirmação do CV gerado com o perfil
   mestre. Se encontrar algo sem respaldo, a candidatura fica como `needs_review` e não é
   enviada automaticamente.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from .config import Candidate
from .llm import ask
from .models import MatchAssessment, TailoredDocs

LANG_NAME = {"fr": "français", "en": "English", "de": "Deutsch"}

CONVENTIONS = """Convenções do mercado (aplique conforme o país da vaga):
- Suíça: CV de 2 páginas no máximo; cabeçalho com nacionalidade e tipo de permis de travail
  (ou "UE/AELE – libre circulation" / "permis B/C/G"), idiomas com nível CECR (A1–C2), taxa de
  ocupação disponível. Foto é comum mas opcional (não gere foto). Datas no formato MM.AAAA.
  "Lettre de motivation" formal: local e data no topo direito, "Madame, Monsieur," se não houver
  nome, 3–4 parágrafos (vous → moi → nous → conclusão), fecho "Je vous prie d'agréer, Madame,
  Monsieur, mes salutations distinguées."
- Luxemburgo: multilíngue (FR/EN/DE/LU) — destaque idiomas; CV de 1–2 páginas; carta mais
  direta, especialmente em EN para finanças/Big4/instituições europeias.
- Sempre: resultados quantificados quando o perfil mestre tiver números; verbos de ação;
  palavras-chave do anúncio usadas literalmente quando correspondem a algo verdadeiro (ATS)."""

TAILOR_SYSTEM = """Você é um especialista em candidaturas para a Suíça romanda e Luxemburgo.
Reescreve CVs e cartas a partir de um PERFIL MESTRE, que é a única fonte da verdade.

Regras inegociáveis:
1. Nunca invente empresas, cargos, datas, diplomas, certificações, números, ferramentas ou
   níveis de idioma. Se algo pedido na vaga não está no perfil mestre, NÃO coloque no CV;
   na carta, no máximo mencione disposição/rapidez para aprender, sem fingir experiência.
2. Você PODE: reordenar seções, escolher quais experiências e realizações destacar,
   renomear competências com o vocabulário do anúncio quando forem a mesma coisa (ex.:
   "gestão de stakeholders" = "gestion des parties prenantes"), reescrever o resumo
   profissional, traduzir.
3. Escreva tudo no idioma da vaga.

{conventions}

## Candidato: {name}
### Preferências/dados pessoais (profile.yaml)
{profile}

### PERFIL MESTRE
{master}

### CV atual (referência de formato e tom)
{current}"""


class Verification(BaseModel):
    unsupported_claims: list[str] = Field(description="afirmações do CV/carta SEM respaldo no perfil mestre")
    ok: bool


VERIFY_SYSTEM = """Você é um auditor de CVs. Recebe um PERFIL MESTRE (verdade) e um CV + carta
gerados. Liste toda afirmação factual (empresa, cargo, data, diploma, certificação, número,
ferramenta, nível de idioma, responsabilidade concreta) que NÃO tenha respaldo no perfil mestre.
Reformulações e traduções de fatos verdadeiros são aceitáveis. ok=true se a lista estiver vazia.

### PERFIL MESTRE
{master}"""


def tailor(job_row, cand: Candidate, assessment: MatchAssessment, step_cfg: dict) -> TailoredDocs:
    lang = job_row["language"] if job_row["language"] in LANG_NAME else "fr"
    system = TAILOR_SYSTEM.format(
        conventions=CONVENTIONS,
        name=cand.name,
        profile=json.dumps(cand.profile, ensure_ascii=False, indent=2),
        master=cand.master_cv,
        current=cand.current_cv,
    )
    return ask(
        system=system,
        user=f"""Adapte o CV e escreva a carta para esta vaga. Idioma: {LANG_NAME[lang]}.

## Análise de aderência já feita
- Lacunas resolvíveis com reescrita: {assessment.gaps_fixable_by_tailoring}
- Palavras-chave a espelhar (somente se verdadeiras): {assessment.keywords_to_mirror}
- Lacunas reais (NÃO esconder com invenção): {assessment.real_gaps}

## Vaga
Título: {job_row['title']}
Empresa: {job_row['company']}
Local: {job_row['location']} ({job_row['country']})

{(job_row['description'] or '')[:20000]}""",
        schema=TailoredDocs,
        step_cfg=step_cfg,
        max_tokens=16000,
    )


def verify(docs: TailoredDocs, cand: Candidate, step_cfg: dict) -> Verification:
    return ask(
        system=VERIFY_SYSTEM.format(master=cand.master_cv),
        user=f"## CV gerado\n{docs.cv_markdown}\n\n## Carta gerada\n{docs.cover_letter_markdown}",
        schema=Verification,
        step_cfg=step_cfg,
        max_tokens=4000,
    )
