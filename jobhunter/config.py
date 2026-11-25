"""Carrega settings.yaml e os perfis dos candidatos (config/candidates/<id>/)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class Candidate:
    id: str
    profile: dict          # profile.yaml: o que procura, restrições, idiomas, permis...
    master_cv: str         # master_cv.md: inventário COMPLETO de carreira (só fatos reais)
    current_cv: str        # current_cv.md: o CV que está sendo usado hoje

    @property
    def name(self) -> str:
        return self.profile.get("name", self.id)


def load_settings(path: Path | None = None) -> dict:
    path = path or CONFIG_DIR / "settings.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} não existe. Copie config/settings.example.yaml para config/settings.yaml e ajuste."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_candidates(settings: dict) -> list[Candidate]:
    out = []
    for cid in settings["candidates"]:
        d = CONFIG_DIR / "candidates" / cid
        out.append(
            Candidate(
                id=cid,
                profile=yaml.safe_load((d / "profile.yaml").read_text(encoding="utf-8")),
                master_cv=(d / "master_cv.md").read_text(encoding="utf-8"),
                current_cv=(d / "current_cv.md").read_text(encoding="utf-8"),
            )
        )
    return out
