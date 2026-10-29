from magi.domain.ballot import Ballot
from magi.domain.types import Persona, Choice


def to_ballot(data: dict, persona: Persona, round: int) -> Ballot:

    choice = Choice(data["choice"])
  
    evidence_refs = tuple(data["evidence_refs"])

    ballot = Ballot(
        persona=persona,
        round=round,
        choice=choice,
        evidence_refs=evidence_refs

    )

    return ballot