"""Camada fina sobre o SDK oficial da Anthropic.

Decisões:
- Saída estruturada (`messages.parse` + Pydantic): o SDK valida o JSON; nada de regex em texto.
- Prompt caching no bloco de sistema: o perfil/CV do candidato é idêntico em centenas de
  chamadas por dia; com cache, essa parte custa ~5% do preço normal a partir da 2ª chamada.
- `fallbacks: "default"`: se um classificador de segurança recusar a requisição (raro, mas
  possível com textos longos de terceiros), a API refaz em outro modelo em vez de falhar.
- O modelo e o "effort" de cada etapa vêm do settings.yaml — triagem pode usar effort baixo
  (ou o Haiku, se quiser economizar), adaptação de CV usa effort alto.
"""

from __future__ import annotations

import logging
from typing import TypeVar

import anthropic
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

    try:
        resp = client().beta.messages.parse(**kwargs)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise FatalAPIError(f"chave da API inválida ou sem permissão: {e}") from e
    except anthropic.BadRequestError as e:
        if any(h in str(e).lower() for h in FATAL_HINTS):
            raise FatalAPIError(f"sem crédito na API da Anthropic (console.anthropic.com → Billing): {e}") from e
        raise

    if resp.stop_reason == "refusal":
        raise LLMRefused(getattr(resp.stop_details, "explanation", "refusal"))
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("resposta cortada por max_tokens; aumente max_tokens dessa etapa")
    u = resp.usage
    log.debug("llm %s in=%s cache_read=%s out=%s", model, u.input_tokens,
              getattr(u, "cache_read_input_tokens", 0), u.output_tokens)
    return resp.parsed_output
