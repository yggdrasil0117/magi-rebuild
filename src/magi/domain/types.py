from enum import Enum


class Persona(Enum):
    MELCHIOR = "MELCHIOR"
    BALTHASAR = "BALTHASAR"
    CASPER = "CASPER"
class Choice(Enum):
    A = "A"
    B = "B"
class CaseStatus(Enum):
    READY="READY"
    ROUND_1="ROUND_1"
    ROUND_2="ROUND_2"
    TERMINATED="TERMINATED"