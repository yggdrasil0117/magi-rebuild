from dataclasses import dataclass

from magi.domain.types import Persona, Choice


@dataclass(frozen=True)
class Ballot:
    persona: Persona
    round:int
    choice:Choice
    reasoning:str
    evidence_refs: tuple[str, ...]=()

    def __post_init__(self):
        if not isinstance(self.persona, Persona):
            raise TypeError("Invalid persona type")
        if type(self.round) is not int:
            raise TypeError("Invalid round type")
        if self.round not in (1, 2):
            raise ValueError("Invalid round value")
        if not isinstance(self.choice,Choice):
            raise TypeError("Invalid Choice type")
        if not isinstance(self.reasoning, str):
            raise TypeError("Invalid reason type")
        if not bool(self.reasoning.strip()):
            raise ValueError("Invalid reason value")
        if not isinstance(self.evidence_refs,tuple):
            raise TypeError("Invalid evidence_refs type")
        if not all(isinstance(evidence,str) for evidence in self.evidence_refs):
            raise TypeError("Invalid evidence value")



