from dataclasses import FrozenInstanceError

import pytest

from magi.domain.ballot import Ballot
from magi.domain.types import Persona, Choice


@pytest.mark.parametrize(
    "persona", [Persona.MELCHIOR, Persona.CASPER, Persona.BALTHASAR]
)
def test_persona(persona):
    ballot = Ballot(persona=persona, round=1, choice=Choice.A, reasoning="wwww")
    assert ballot.persona == persona


@pytest.mark.parametrize("round_number", [1, 2])
def test_round(round_number):
    ballot = Ballot(persona=Persona.MELCHIOR, round=round_number, choice=Choice.A, reasoning="wwww")
    assert ballot.round == round_number


def test_invalid_persona():
    with pytest.raises(TypeError):
        Ballot(persona="HELLO", round=1, choice=Choice.A, reasoning="wwww")


def test_ballot_immutable():
    ballot = Ballot(persona=Persona.MELCHIOR, round=1, choice=Choice.A, reasoning="wwww")
    with pytest.raises(FrozenInstanceError):
        ballot.persona = Persona.CASPER


@pytest.mark.parametrize("round_number", [0, 3])
def test_invalid_round(round_number):
    with pytest.raises(ValueError):
        Ballot(persona=Persona.MELCHIOR, round=round_number, choice=Choice.A, reasoning="wwww")


@pytest.mark.parametrize("round_number", [True, 1.0, "1"])
def test_invalid_round_type(round_number):
    with pytest.raises(TypeError):
        Ballot(persona=Persona.BALTHASAR, round=round_number, choice=Choice.A, reasoning="wwww")


@pytest.mark.parametrize("choice", [Choice.A, Choice.B])
def test_choice(choice):
    ballot = Ballot(persona=Persona.MELCHIOR, round=1, choice=choice, reasoning="wwww")
    assert ballot.choice == choice


@pytest.mark.parametrize("choice", ["A", "B"])
def test_invalid_choice(choice):
    with pytest.raises(TypeError):
        Ballot(persona=Persona.MELCHIOR, round=1, choice=choice, reasoning="wwww")


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_invalid_reason(reason):
    with pytest.raises(ValueError):
        Ballot(persona=Persona.MELCHIOR, round=1, choice=Choice.A, reasoning=reason)


@pytest.mark.parametrize("evidence", [
    ["doc-1"],
    ("doc-1", 123),
])
def test_invalid_evidence_type(evidence):
    with pytest.raises(TypeError):
        Ballot(persona=Persona.BALTHASAR, round=1, choice=Choice.A, reasoning="wwww", evidence_refs=evidence)
