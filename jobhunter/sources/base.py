from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Job


class Source(ABC):
    name: str = "base"
    # national=True: o site busca o país inteiro de uma vez (jobs.ch, jobup.ch) e é chamado
    # uma vez por termo e país, não uma vez por cidade.
    national: bool = False
    # ids externos já gravados no banco; a fonte pode pular o download do detalhe deles
    known_ids: set[str] = set()

    @abstractmethod
    def search(self, query: str, location: str, country: str) -> list[Job]:
        """Busca vagas para um termo + local. Deve devolver descrições completas quando possível."""
