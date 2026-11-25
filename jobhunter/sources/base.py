from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Job


class Source(ABC):
    name: str = "base"

    @abstractmethod
    def search(self, query: str, location: str, country: str) -> list[Job]:
        """Busca vagas para um termo + local. Deve devolver descrições completas quando possível."""
