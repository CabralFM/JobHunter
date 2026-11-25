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
3. Escreva tudo no idioma do documento indicado no pedido.
5. NÃO calcule números novos: nada de "X anos de experiência em Y", totais ou somas de
   períodos, a não ser que o número esteja escrito no perfil mestre/profile.yaml.
6. NÃO atribua ao candidato competências comportamentais ou estilos de trabalho só porque o
   anúncio pede (ex.: "influencing without authority", "thrives in ambiguity"). Use apenas os
   que estiverem no perfil mestre.
7. NÃO combine dois fatos separados num fato novo (ex.: "departamento de 40 pessoas" + "análises
   AML" NÃO vira "departamento AML de 40 pessoas"). Cada fato como está escrito na fonte.
8. Mobilidade/relocalização: use exatamente o que diz o profile.yaml (campo mobility).
9. Lacunas reais: na carta, no máximo UMA frase curta e positiva (ex.: "I am ramping up on X"),
   nunca um parágrafo listando o que falta.
4. Título do CV (linha logo abaixo do nome): ajuste ao cargo da vaga quando o perfil mestre
   sustenta esse título (ex.: vaga "Data Governance Analyst" -> "Data Governance Analyst";
   "Analyste Risque de Crédit" -> título equivalente), mantendo o nível de senioridade real.
   Se a vaga pede um cargo que o candidato não exerceu, use o título padrão do profile.yaml
   (`default_headline`). Título curto: só o cargo, sem lista de especialidades.

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


VERIFY_SYSTEM = """Você é um auditor de CVs. Recebe as FONTES DA VERDADE do candidato (perfil
mestre + dados pessoais/preferências do profile.yaml) e um CV + carta gerados. Liste toda
afirmação factual (empresa, cargo exercido, data, diploma, certificação, número, ferramenta, nível
de idioma, responsabilidade concreta) que NÃO tenha respaldo nessas fontes.

NÃO são invenções (não liste):
- reformulações, sínteses e traduções de fatos verdadeiros;
- o TÍTULO/headline logo abaixo do nome ajustado ao cargo da vaga, desde que coerente com a
  experiência real (é uma regra do sistema; ex.: "Business Analyst" para quem faz levantamento
  de requisitos e especificações);
- dados de contato, nacionalidade, situação de permis, disponibilidade e mobilidade que estejam
  no profile.yaml;
- motivação e interesse expressos na carta (ex.: "je suis motivé par...").
Seja específico: cite o trecho exato e por que não tem respaldo. ok=true se a lista estiver vazia.

### PERFIL MESTRE
{master}

### PROFILE.YAML (dados pessoais e preferências)
{profile}"""


def documents_language(job_row, cand: Candidate) -> str:
    """Idioma do CV e da carta.

    Padrão: o idioma do anúncio. O candidato pode fixar por país em profile.yaml, ex.:
        documents_language: {CH: fr, LU: job}
    ("job" = seguir o anúncio). Útil porque na Suíça romanda muitos recrutadores esperam o
    dossier em francês mesmo quando o anúncio está em inglês.
    """
    job_lang = job_row["language"] if job_row["language"] in LANG_NAME else "fr"
    policy = (cand.profile.get("documents_language") or {}).get(job_row["country"], "job")
    return policy if policy in LANG_NAME else job_lang


def tailor(job_row, cand: Candidate, assessment: MatchAssessment, step_cfg: dict) -> TailoredDocs:
    lang = documents_language(job_row, cand)
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
        system=VERIFY_SYSTEM.format(master=cand.master_cv,
                                    profile=json.dumps(cand.profile, ensure_ascii=False, indent=2)),
        user=f"## CV gerado\n{docs.cv_markdown}\n\n## Carta gerada\n{docs.cover_letter_markdown}",
        schema=Verification,
        step_cfg=step_cfg,
        max_tokens=4000,
    )
