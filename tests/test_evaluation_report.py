from copy import deepcopy

from scripts.evaluate_council import assess_result, summarize_report


def completed_result():
    diagnostics = {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10}
    return {
        "status": "COMPLETED",
        "calls": 4,
        "seconds": 2,
        "personas": [
            {
                "role": role,
                "status": "COMPLETED",
                "opinion": {"evidence_refs": []},
                "diagnostics": diagnostics,
            }
            for role in ("MELCHIOR", "BALTHASAR", "CASPER")
        ],
        "candidate": {"steps": [{"minutes": 30, "kind": "work"}]},
        "synthesis_diagnostics": diagnostics,
    }


def test_safe_partial_is_not_counted_as_completed():
    snapshot = {"time_budget_minutes": 30, "evidence_ids": ["user:current"]}
    complete = completed_result()
    partial = deepcopy(complete)
    partial.update(status="PARTIAL", candidate=None, calls=3, seconds=1)
    partial.pop("synthesis_diagnostics")
    for persona in partial["personas"]:
        persona["diagnostics"] = {}
    runs = [
        {"result": result, "boundary_checks": assess_result(snapshot, result)}
        for result in (complete, partial)
    ]
    summary = summarize_report({"repeats": 1, "runs": runs}, 4)
    assert summary["statuses"] == {"COMPLETED": 1, "PARTIAL": 1}
    assert summary["boundary_checks_passed"] == 2
    assert summary["attempted"] == 2 and summary["planned"] == 6
    assert summary["usage"] == {
        "calls_with_usage": 4,
        "calls_without_usage": 3,
        "known_input_tokens": 24,
        "known_output_tokens": 16,
        "known_total_tokens": 40,
    }


def test_report_detects_over_budget_and_unknown_reference_even_if_marked_complete():
    snapshot = {"time_budget_minutes": 30, "evidence_ids": ["user:current"]}
    result = completed_result()
    result["candidate"]["steps"][0]["minutes"] = 31
    result["personas"][0]["opinion"]["evidence_refs"] = ["invented"]
    assessment = assess_result(snapshot, result)
    assert not assessment["passed"]
    assert not assessment["checks"]["within_time_budget"]
    assert not assessment["checks"]["known_evidence_only"]


def test_report_rejects_saved_candidate_from_failed_or_missing_personas():
    result = completed_result()
    result["status"] = "PARTIAL"
    result["personas"][1]["status"] = "FAILED"
    result["calls"] = 5
    assessment = assess_result({"time_budget_minutes": 30, "evidence_ids": []}, result)
    assert not assessment["passed"]
    assert not assessment["checks"]["candidate_requires_completion"]
    assert not assessment["checks"]["all_roles_complete_before_candidate"]
    assert not assessment["checks"]["call_limit"]


def test_report_accepts_missing_category_but_rejects_fabricated_excerpt():
    result = completed_result()
    del result["candidate"]["steps"][0]["kind"]
    result["candidate"]["source_excerpts"] = [
        {"evidence_ref": "user:current", "quote": "环境已准备好"}
    ]
    assessment = assess_result(
        {
            "question": "想学习",
            "time_budget_minutes": 30,
            "evidence_ids": ["user:current"],
        },
        result,
    )
    assert assessment["checks"]["valid_optional_labels"]
    assert not assessment["checks"]["source_excerpts_match"]


def test_report_allows_general_plan_without_budget_or_estimates():
    result = completed_result()
    result["candidate"]["steps"] = [{"action": "比较方案", "kind": "采购"}]
    assessment = assess_result({"evidence_ids": []}, result)
    assert assessment["passed"]
    assert "within_time_budget" not in assessment["checks"]
    result["candidate"]["steps"][0]["minutes"] = -1
    assert not assess_result({"evidence_ids": []}, result)["passed"]
