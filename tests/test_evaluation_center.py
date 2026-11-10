"""验证对照拒绝混用案例，并保留失败一侧的诊断。"""

import pytest

from scripts.evaluation_center import compare, read_evidence


def test_junit_failure_is_visible_in_version_comparison(tmp_path):
    """子进程失败仍可以与同一组完整案例比较。"""
    report = tmp_path / "evidence.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="research" name="privacy" time="0.2">'
        '<failure message="expired memory entered context"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    cases, metrics, model = read_evidence(report, "junit")
    failed = dict(
        schema=1,
        suite="research-offline",
        id="first",
        commit="old",
        dirty=False,
        status="FAILED",
        suite_hash="same",
        cases=cases,
        metrics=metrics,
        model=model,
    )
    passed = failed | dict(
        id="second",
        commit="new",
        status="FINISHED",
        cases={"research::privacy": {"status": "PASSED"}},
        metrics=metrics | {"cases.FAILURE": 0, "cases.PASSED": 1},
    )
    difference = compare(failed, passed)
    assert difference["metrics"]["cases.FAILURE"]["delta"] == -1
    assert difference["cases"]["research::privacy"]["before"]["detail"] == (
        "expired memory entered context"
    )
    for change, reason in (
        ({"suite_hash": "changed"}, "suite_hash"),
        ({"dirty": True}, "干净版本"),
        ({"cases": {}}, "证据不完整"),
    ):
        with pytest.raises(ValueError, match=reason):
            compare(failed, passed | change)
