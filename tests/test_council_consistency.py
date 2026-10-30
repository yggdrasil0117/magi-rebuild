"""Offline allocation regressions; these do not measure live model quality."""

import json
from copy import deepcopy

import pytest

pytest.importorskip("langchain_ollama")

from magi.council.generation import generation_schema
from magi.council.service import Candidate, Council, Opinion, Synthesis, candidate_body
from magi.harness.config import HarnessConfig


def run_candidate(tmp_path, candidate, **changes):
    snapshot = {
        "question": "只剩90分钟，工具调用没看懂，请保留休息",
        "time_budget_minutes": 90,
        "memories": [{"key": "style", "value": "先解释依据"}],
        "evidence_ids": ["user:current", "memory:style"],
    } | changes

    def invoke(system, payload, schema, *args):
        if schema is Opinion:
            return json.dumps(
                dict(
                    view="依据",
                    recommendation="先定位",
                    concerns=[],
                    unknowns=[],
                    evidence_refs=[],
                )
            )
        return json.dumps(
            dict(
                agreement=["减负"], disagreements=[], unknowns=[], candidate=candidate
            ),
            ensure_ascii=False,
        )

    settings = HarnessConfig(
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
    )
    return Council(settings, invoke).run(snapshot)


def candidate_for(blocks):
    return {
        "title": "调整安排",
        "steps": [
            dict(kind=kind, action=action, minutes=minutes, done_when=done)
            for kind, action, minutes, done in blocks
        ],
        "revision_reason": "保留定位与休息，暂缓扩展任务",
        "assumptions": ["环境可正常启动"],
        "source_excerpts": [
            {"evidence_ref": "user:current", "quote": "工具调用没看懂"}
        ],
        "unknowns": ["具体报错位置"],
    }


# Explicit replacements for the five ambiguous allocations documented in 007.
# The replacement is authored by us, not claimed as a model-generated repair.
@pytest.mark.parametrize(
    "blocks,budget,totals",
    [
        (
            [
                ("work", "阅读", 30, "画出调用链"),
                ("rest", "休息", 10, "停止学习"),
                ("work", "调试", 50, "记录工具输入输出"),
            ],
            90,
            [80, 10, 0, 0],
        ),
        (
            [
                ("work", "配送下单", 10, "订单提交"),
                ("rest", "休息", 45, "停止任务"),
                ("buffer", "预留", 5, "不承诺任务产出"),
            ],
            60,
            [10, 45, 0, 5],
        ),
        (
            [
                ("work", "实验", 150, "记录结果"),
                ("work", "求职", 120, "修改简历"),
                ("rest", "休息", 60, "停止任务"),
                ("review", "复盘", 30, "写出下一步"),
            ],
            360,
            [270, 60, 30, 0],
        ),
        (
            [
                ("work", "确定主次", 30, "选定重点"),
                ("work", "练习", 150, "修改接口"),
                ("rest", "休息", 30, "停止学习"),
                ("review", "回顾", 30, "解释调用链"),
            ],
            240,
            [180, 30, 30, 0],
        ),
        (
            [("work", "配送下单", 10, "订单提交"), ("rest", "休息", 50, "停止任务")],
            60,
            [10, 50, 0, 0],
        ),
        # Unseen case: leave spare budget, with all four categories.
        (
            [
                ("work", "预约维修", 7, "获得预约号"),
                ("rest", "休息", 13, "停止任务"),
                ("review", "记录", 4, "记录故障"),
                ("buffer", "等待余量", 6, "无需产出"),
            ],
            40,
            [7, 13, 4, 6],
        ),
    ],
)
def test_explicit_allocations_survive_generation_and_render(
    tmp_path, blocks, budget, totals
):
    result = run_candidate(tmp_path, candidate_for(blocks), time_budget_minutes=budget)
    assert result["status"] == "COMPLETED" and result["calls"] == 4
    actual = result["synthesis_diagnostics"]["minutes_by_kind"]
    assert [actual.get(k, 0) for k in ("work", "rest", "review", "buffer")] == totals
    body = candidate_body(Candidate(**result["candidate"]))
    assert f"合计 {sum(totals)} 分钟" in body
    assert "用户陈述依据" in body and "模型假设" in body and "待确认事项" in body


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing_minutes", "MISSING_STEP_MINUTES"),
        ("invented_quote", "UNMATCHED_SOURCE_EXCERPT"),
        ("invented_source", "UNMATCHED_SOURCE_EXCERPT"),
        ("paraphrase", "UNMATCHED_SOURCE_EXCERPT"),
        ("over_budget", "TIME_BUDGET_EXCEEDED"),
        ("invalid_kind", "INVALID_FIELDS"),
    ],
)
def test_inconsistent_candidate_has_no_saveable_result(tmp_path, mutation, code):
    candidate = candidate_for([("work", "定位", 30, "记录卡点")])
    if mutation == "missing_minutes":
        del candidate["steps"][0]["minutes"]
    elif mutation == "invented_quote":
        candidate["source_excerpts"][0]["quote"] = "环境已经就绪"
    elif mutation == "invented_source":
        candidate["source_excerpts"][0]["evidence_ref"] = "MELCHIOR"
    elif mutation == "paraphrase":
        candidate["source_excerpts"][0]["quote"] = "我不懂Agent"
    elif mutation == "over_budget":
        candidate["steps"][0]["minutes"] = 91
    else:
        candidate["steps"][0]["kind"] = 123
    result = run_candidate(tmp_path, candidate)
    assert result["status"] == "PARTIAL" and result["candidate"] is None
    assert result["synthesis_error_code"] == code and result["calls"] == 4


def test_no_budget_allows_missing_minutes_and_memory_quote_is_allowed(tmp_path):
    candidate = candidate_for([("review", "复盘", 12, "写出下一步")])
    candidate["source_excerpts"] = [
        {"evidence_ref": "memory:style", "quote": "解释依据"}
    ]
    assert (
        run_candidate(tmp_path, candidate, time_budget_minutes=None)["status"]
        == "COMPLETED"
    )
    del candidate["steps"][0]["minutes"]
    assert (
        run_candidate(tmp_path, candidate, time_budget_minutes=None)["status"]
        == "COMPLETED"
    )
    schema = generation_schema(
        Synthesis, {"time_budget_minutes": None, "evidence_ids": []}
    )
    step = schema["properties"]["candidate"]["properties"]["steps"]["items"]
    assert "minutes" not in step["required"] and "kind" not in step["required"]
    assert {"type": "null"} in step["properties"]["minutes"]["anyOf"]


def test_historical_candidate_remains_readable_without_inventing_categories():
    candidate = deepcopy(candidate_for([("work", "旧步骤", 20, "记录")]))
    del candidate["steps"][0]["kind"]
    del candidate["source_excerpts"]
    del candidate["unknowns"]
    body = candidate_body(Candidate(**candidate))
    assert "旧版候选" not in body
    assert "未分类 20 分钟" in body


@pytest.mark.parametrize("minutes", [True, "30", 1.5])
def test_minutes_are_actual_integers(tmp_path, minutes):
    candidate = candidate_for([("work", "定位", minutes, "记录")])
    result = run_candidate(tmp_path, candidate)
    assert result["candidate"] is None
    assert result["synthesis_error_code"] == "INVALID_FIELDS"


@pytest.mark.parametrize(
    "action,done,label",
    [
        ("比较两种采购方案", "列出选择理由和缺失报价", "采购比较"),
        ("定位接口错误", "记录最小复现和报错位置", "debug"),
        ("明确出行需求", "确认目的地与同行者要求", None),
    ],
)
def test_general_plans_do_not_require_time_or_fixed_categories(
    tmp_path, action, done, label
):
    candidate = candidate_for([("work", action, 30, done)])
    step = candidate["steps"][0]
    step.pop("minutes")
    step.pop("kind")
    if label is not None:
        step["kind"] = label
    candidate["source_excerpts"] = []
    result = run_candidate(
        tmp_path, candidate, question=action, time_budget_minutes=None
    )
    assert result["status"] == "COMPLETED" and result["calls"] == 4
    body = candidate_body(Candidate(**result["candidate"]))
    assert action in body and done in body
    assert "合计" not in body and "旧版候选" not in body
    assert result["synthesis_diagnostics"]["minutes_by_kind"] == {}


def test_explicit_budget_needs_minutes_but_not_categories(tmp_path):
    candidate = candidate_for([("work", "定位", 30, "记录卡点")])
    del candidate["steps"][0]["kind"]
    result = run_candidate(tmp_path, candidate)
    assert result["status"] == "COMPLETED"
    assert result["synthesis_diagnostics"]["total_minutes"] == 30
    assert result["synthesis_diagnostics"]["minutes_by_kind"] == {"未分类": 30}


def test_partial_optional_estimates_do_not_present_a_complete_total(tmp_path):
    candidate = candidate_for(
        [("debug", "定位", 30, "找到复现"), ("实现", "修复", None, "验证复现消失")]
    )
    result = run_candidate(tmp_path, candidate, time_budget_minutes=None)
    assert result["status"] == "COMPLETED"
    body = candidate_body(Candidate(**result["candidate"]))
    assert "部分步骤未估时" in body and "合计" not in body
    assert result["synthesis_diagnostics"]["minutes_by_kind"] == {}


def test_explicit_null_optional_fields_and_old_minimal_candidate(tmp_path):
    candidate = candidate_for([("work", "比较", 30, "记录取舍")])
    candidate["steps"][0].update(minutes=None, kind=None)
    assert (
        run_candidate(tmp_path, candidate, time_budget_minutes=None)["status"]
        == "COMPLETED"
    )
    for key in ("minutes", "kind"):
        del candidate["steps"][0][key]
    for key in ("source_excerpts", "unknowns"):
        del candidate[key]
    assert (
        run_candidate(tmp_path, candidate, time_budget_minutes=None)["status"]
        == "COMPLETED"
    )
