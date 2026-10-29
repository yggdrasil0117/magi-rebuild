from magi.domain.ballot import Ballot
from magi.domain.types import Persona, CaseStatus


class DecisionCase:

    def __init__(self):
        self._ballots: dict[tuple[Persona, int], Ballot] = {}
        self._status:CaseStatus=CaseStatus.READY

    def _has_ballot(self, persona: Persona, round_number: int) -> bool:
        key = (persona, round_number)
        return key in self._ballots

    def _ensure_unique_ballot(self, ballot: Ballot) -> None:
        persona = ballot.persona
        round_number = ballot.round
        if  self._has_ballot(persona,round_number):
            raise ValueError("Duplicate ballot")

    def start_round_1(self) -> None:
        if self._status is CaseStatus.READY:
            self._status = CaseStatus.ROUND_1
        else:
            raise ValueError("invalid stage")

    def add_ballot(self, ballot: Ballot) -> None:
        if not self._status is CaseStatus.ROUND_1:
            raise ValueError("invalid stage")
        if ballot.round !=1:
            raise ValueError("invalid stage")
        self._ensure_unique_ballot(ballot)
        key = (ballot.persona,ballot.round)
        self._ballots[key] = ballot

    def _has_all_round_1_ballots(self) -> bool:

        return (self._has_ballot(Persona.MELCHIOR, 1)and
                self._has_ballot(Persona.CASPER,1)and
                self._has_ballot(Persona.BALTHASAR, 1))

    def _ensure_in_round_1(self) -> None:
        if self._status is not CaseStatus.ROUND_1:
           raise ValueError("invalid round")




