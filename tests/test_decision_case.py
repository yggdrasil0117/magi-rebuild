import pytest

from magi.domain.ballot import Ballot
from magi.domain.decision_case import DecisionCase
from magi.domain.types import CaseStatus, Persona, Choice


def test_start_round_1_twice():
    case = DecisionCase()
    case.start_round_1()
    with pytest.raises(ValueError):
        case.start_round_1()
    assert case._status == CaseStatus.ROUND_1


def test_add_ballot():
    case = DecisionCase()
    case.start_round_1()
    ballot = Ballot(persona=Persona.MELCHIOR,
                    round=1,
                    choice=Choice.A,
                    reasoning="wwww")
    case.add_ballot(ballot)
    key = (Persona.MELCHIOR, 1)
    assert case._ballots[key] is ballot


def test_duplicate_ballot():
    case = DecisionCase()
    case.start_round_1()
    ballot1 = Ballot(persona=Persona.MELCHIOR,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    case.add_ballot(ballot1)
    ballot2 = Ballot(persona=Persona.MELCHIOR,
                     round=1,
                     choice=Choice.B,
                     reasoning="wwww")
    with pytest.raises(ValueError):
        case.add_ballot(ballot2)
    key = (Persona.MELCHIOR, 1)
    assert case._ballots[key] is ballot1


def test_add_ballot_before_round_1():
    case = DecisionCase()
    ballot1 = Ballot(persona=Persona.MELCHIOR,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    with pytest.raises(ValueError):
        case.add_ballot(ballot1)
    assert case._status is CaseStatus.READY
    assert case._ballots == {}


def test_has_all_round_1_ballots():
    case = DecisionCase()
    case.start_round_1()
    ballot1 = Ballot(persona=Persona.MELCHIOR,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    ballot2 = Ballot(persona=Persona.CASPER,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    ballot3 = Ballot(persona=Persona.BALTHASAR,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    case.add_ballot(ballot1)
    case.add_ballot(ballot2)
    case.add_ballot(ballot3)
    assert case._has_all_round_1_ballots() == True


def test_missing_round_1_ballot():
    case = DecisionCase()
    case.start_round_1()
    ballot1 = Ballot(persona=Persona.MELCHIOR,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    ballot2 = Ballot(persona=Persona.CASPER,
                     round=1,
                     choice=Choice.A,
                     reasoning="wwww")
    case.add_ballot(ballot1)
    case.add_ballot(ballot2)
    assert not case._has_all_round_1_ballots()


def test_ensure_in_round_1_rejects_ready_case():
    case = DecisionCase()

    with pytest.raises(ValueError):
        case._ensure_in_round_1()
    assert case._status == CaseStatus.READY
