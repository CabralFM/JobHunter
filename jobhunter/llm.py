"""Camada fina sobre o SDK oficial da Anthropic.

Decisões:
- Saída estruturada (`messages.parse` + Pydantic): o SDK valida o JSON; nada de regex em texto.
- Prompt caching no bloco de sistema: o perfil/CV do candidato é idêntico em centenas de
  chamadas por dia; com cache, essa parte custa ~5% do preço normal a partir da 2ª chamada.
- `fallbacks: "default"`: se um classificador de segurança recusar a requisição (raro, mas
  possível com textos longos de terceiros), a API refaz em outro modelo em vez de falhar
  (não existe no Haiku; lá a recusa só pula a vaga).
- Orçamento: o custo de cada chamada é somado em data/spend.json; passou do teto mensal
  (settings.yaml -> budget.monthly_usd), tudo para até o mês seguinte.
- O modelo e o "effort" de cada etapa vêm do settings.yaml — triagem pode usar effort baixo
  (ou o Haiku, se quiser economizar), adaptação de CV usa effort alto.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TypeVar

import anthropic
import pydantic
from pydantic import BaseModel

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # lê ANTHROPIC_API_KEY (ou perfil do `ant auth login`)
    return _client


class LLMRefused(RuntimeError):
    pass


class FatalAPIError(RuntimeError):
    """Erro que não adianta repetir (sem crédito, chave inválida): a execução deve parar."""


FATAL_HINTS = ("credit balance", "invalid x-api-key", "authentication", "permission")
LIMIT_HINTS = ("usage limit", "spend limit", "regain access")


def _fatal_or_same(e: Exception) -> Exception:
    """Converte erros 400 que não adianta repetir em FatalAPIError, com uma mensagem útil."""
    msg = str(e).lower()
    if any(h in msg for h in LIMIT_HINTS):
        return FatalAPIError(
            "limite de gasto mensal configurado no console da Anthropic foi atingido "
            "(console.anthropic.com → Settings → Limits → aumente o 'monthly spend limit'): "
            f"{e}")
    if any(h in msg for h in FATAL_HINTS):
        return FatalAPIError(
            f"sem crédito na API da Anthropic (console.anthropic.com → Billing): {e}")
    return e

# Preço em US$ por 1 milhão de tokens: (entrada, saída, leitura de cache, escrita de cache).
# Fonte: tabela de preços da Anthropic (nov/2026). Modelos fora da tabela contam como Opus.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}

# Orçamento mensal: cada chamada soma o custo real (pelos tokens que a API devolve) em
# data/spend.json; ao atingir o teto, a execução para como se o crédito tivesse acabado.
_budget = {"path": None, "monthly_usd": None}


def set_budget(data_dir: Path, monthly_usd: float | None) -> None:
    _budget["path"] = Path(data_dir) / "spend.json"
    _budget["monthly_usd"] = monthly_usd


def _month() -> str:
    return date.today().strftime("%Y-%m")


def _load_spend() -> dict:
    path = _budget["path"]
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def spent_this_month() -> float:
    return float(_load_spend().get(_month(), 0.0))


def call_cost(model: str, usage) -> float:
    pin, pout, pread, pwrite = PRICES.get(model, PRICES["claude-opus-5-5"])
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * pin + usage.output_tokens * pout
            + read * pread + write * pwrite) / 1_000_000


def _add_spend(usd: float) -> None:
    path = _budget["path"]
    if not path:
        return
    data = _load_spend()
    data[_month()] = round(float(data.get(_month(), 0.0)) + usd, 6)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _check_budget() -> None:
    cap = _budget["monthly_usd"]
    if cap is not None and spent_this_month() >= cap:
        raise FatalAPIError(
            f"orçamento do mês atingido (US$ {spent_this_month():.2f} de US$ {cap:.2f}); "
            "aumente budget.monthly_usd no settings.yaml ou espere o próximo mês")


def ask(
    *,
    system: str,
    user: str,
    schema: type[T],
    step_cfg: dict,
    max_tokens: int = 16000,
) -> T:
    """Uma chamada com saída estruturada. `step_cfg` = {"model": ..., "effort": ...}."""
    model = step_cfg.get("model", "claude-opus-5-5")
    _check_budget()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        output_config={"effort": step_cfg.get("effort", "medium")},
    )
    if step_cfg.get("fallbacks", True) and not model.startswith("claude-haiku"):
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"

    # Uma resposta cortada (o pensamento do modelo gastou o limite de tokens) chega como JSON
    # incompleto e o SDK levanta ValidationError antes de vermos stop_reason. Nesse caso
    # tentamos de novo uma vez com o dobro do limite.
    for attempt in (1, 2):
        try:
            resp = client().beta.messages.parse(**kwargs)
            break
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise FatalAPIError(f"chave da API inválida ou sem permissão: {e}") from e
        except anthropic.BadRequestError as e:
            raise _fatal_or_same(e) from e
        except pydantic.ValidationError:
            # A chamada falhou mas foi cobrada: conta o pior caso (limite inteiro como saída).
            _add_spend(kwargs["max_tokens"] * PRICES.get(model, PRICES["claude-opus-5-5"])[1] / 1_000_000)
            if attempt == 2:
                raise RuntimeError("resposta cortada duas vezes (JSON incompleto); "
                                   "tente retailor com --model claude-sonnet-5-5")
            log.info("resposta cortada; repetindo com max_tokens=%d", kwargs["max_tokens"] * 2)
            kwargs["max_tokens"] *= 2

    if resp.stop_reason == "refusal":
        raise LLMRefused(getattr(resp.stop_details, "explanation", "refusal"))
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("resposta cortada por max_tokens; aumente max_tokens dessa etapa")
    u = resp.usage
    _add_spend(call_cost(model, u))
    log.debug("llm %s in=%s cache_read=%s out=%s", model, u.input_tokens,
              getattr(u, "cache_read_input_tokens", 0), u.output_tokens)
    return resp.parsed_output
