"""Expansão de títulos: o problema "a vaga tem a ver comigo mas o título é outro".

Em vez de buscar só "Data Analyst", pedimos ao Claude, a partir do perfil mestre e do que o
candidato procura, a lista de títulos equivalentes em FR/EN/DE usados de fato no mercado
suíço/luxemburguês. Ex.: um perfil de "Product Owner" também deve buscar "Business Analyst
IT", "Chef de projet digital", "Responsable de produit", "Fachspezialist Digitalisierung"...

Isso é a 1ª camada (ampliar o funil). A 2ª camada é o match semântico (matcher.py), que lê a
descrição inteira e ignora o título — é ela que pega as vagas com nomes "estranhos".

O resultado fica em cache (data/search_terms.json) e só é refeito quando o perfil muda,
porque os termos não precisam mudar todo dia e assim você pode revisá-los/editá-los à mão.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Candidate
from .llm import ask


class SearchTerm(BaseModel):
    term: str = Field(description="termo curto como se digita na busca do site (2-4 palavras)")
    language: str = Field(description="fr | en | de")
    rationale: str


class SearchPlan(BaseModel):
    terms: list[SearchTerm]


SYSTEM = """Você é um recrutador sênior especializado no mercado de trabalho da Suíça romanda
(Genève, Vaud, Neuchâtel, Fribourg, Valais) e de Luxemburgo. Conhece os títulos de cargo que as
empresas desses mercados REALMENTE usam em francês, inglês e alemão, inclusive variações de
nomenclatura entre bancos, multinacionais, setor público, ONGs internacionais e PMEs."""


def build_terms(cand: Candidate, step_cfg: dict, cache_dir: Path, max_terms: int = 15) -> list[str]:
    fingerprint = hashlib.sha1((cand.master_cv + json.dumps(cand.profile, sort_keys=True)).encode()).hexdigest()
    cache = cache_dir / f"search_terms_{cand.id}.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = {}  # arquivo corrompido (ex.: gravação interrompida) -> gera de novo
        if data.get("terms") and (data.get("fingerprint") == fingerprint or data.get("locked")):
            return data["terms"]

    manual = cand.profile.get("search", {}).get("extra_terms", [])
    plan = ask(
        system=SYSTEM,
        user=f"""Gere até {max_terms} termos de busca para encontrar vagas compatíveis com este candidato.

Regras:
- Inclua os cargos-alvo declarados E cargos adjacentes onde a experiência real dele é valorizada,
  mesmo com outro nome (transferable roles).
- Misture FR e EN (e DE só se o candidato tiver alemão utilizável); priorize FR para Suíça romanda.
- Evite termos genéricos demais ("manager", "consultant" sozinho) e cargos que ele declarou não querer.
- Não repita variações triviais (singular/plural, masculino/feminino).

## O que o candidato procura (profile.yaml)
{json.dumps(cand.profile, ensure_ascii=False, indent=2)}

## Perfil mestre (fatos de carreira)
{cand.master_cv}""",
        schema=SearchPlan,
        step_cfg=step_cfg,
        max_tokens=4000,
    )
    terms = list(dict.fromkeys(manual + [t.term for t in plan.terms]))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fingerprint": fingerprint, "locked": False, "terms": terms,
                                 "details": [t.model_dump() for t in plan.terms]}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return terms
