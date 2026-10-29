import hashlib
import json
from types import SimpleNamespace

import pytest

from magi.agent.investigation import EvidenceSession, render_report, validate_report
from magi.agent.investigation import generate_report


def test_report_generation_uses_schema_without_tools():
    requests = []

    def chat(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(valid_report())))

    evidence = {"E1": {"path": "note.txt", "content": "支持读取"}}
    result = generate_report(chat=chat, model="fake", question="当前进度", evidence=evidence)
    assert result == valid_report()
    assert len(requests) == 1
    assert "tools" not in requests[0]
    refs = requests[0]["format"]["properties"]["implemented"]["items"]["properties"]["evidence_ids"]
    assert refs["items"]["enum"] == ["E1"]


def test_report_generation_still_rejects_fabricated_reference():
    def chat(**kwargs):
        report = valid_report()
        report["implemented"][0]["evidence_ids"] = ["E99"]
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(report)))

    with pytest.raises(ValueError, match="未读取"):
        generate_report(chat=chat, model="fake", question="进度", evidence={"E1": {}})


def test_successful_read_registers_exact_content(tmp_path):
    (tmp_path / "note.txt").write_text("资料", encoding="utf-8")
    session = EvidenceSession(tmp_path)
    session.execute("list_files", {"path": "."})
    assert session.records == {}
    result = json.loads(session.execute("read_file", {"path": "note.txt"}))
    assert result == {
        "evidence_id": "E1", "path": "note.txt", "content": "资料",
        "sha256": hashlib.sha256("资料".encode()).hexdigest(),
    }
    records = session.records
    records.clear()
    assert "E1" in session.records


def test_failed_read_is_not_evidence(tmp_path):
    session = EvidenceSession(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        session.execute("read_file", {"path": "missing.txt"})
    assert session.records == {}


def test_read_budget_is_enforced(tmp_path):
    (tmp_path / "note.txt").write_text("内容", encoding="utf-8")
    session = EvidenceSession(tmp_path, max_reads=1)
    session.execute("read_file", {"path": "note.txt"})
    with pytest.raises(ValueError, match="预算"):
        session.execute("read_file", {"path": "note.txt"})
    assert len(session.records) == 1


def valid_report():
    return {"implemented": [{"claim": "支持读取", "evidence_ids": ["E1"]}],
            "remaining": [], "uncertainties": ["未运行代码"]}


def test_valid_report_renders_program_owned_path():
    report = valid_report()
    evidence = {"E1": {"path": "note.txt"}}
    assert validate_report(json.dumps(report), evidence) == report
    assert "[来源：note.txt]" in render_report(report, evidence)


@pytest.mark.parametrize("change", ["unknown", "empty_refs", "blank_claim", "extra", "bad_unknowns"])
def test_invalid_reports_are_rejected(change):
    report = valid_report()
    if change == "unknown":
        report["implemented"][0]["evidence_ids"] = ["E99"]
    elif change == "empty_refs":
        report["implemented"][0]["evidence_ids"] = []
    elif change == "blank_claim":
        report["implemented"][0]["claim"] = " "
    elif change == "extra":
        report["invented_path"] = "fake.txt"
    else:
        report["uncertainties"] = "不知道"
    with pytest.raises(ValueError):
        validate_report(json.dumps(report), {"E1": {"path": "note.txt"}})


def test_no_read_cannot_pass_report_acceptance():
    with pytest.raises(ValueError, match="没有成功读取"):
        validate_report(json.dumps(valid_report()), {})


@pytest.mark.parametrize("content", ["不是 JSON", "[]", "null"])
def test_bad_report_structure(content):
    with pytest.raises(ValueError):
        validate_report(content, {"E1": {"path": "note.txt"}})
