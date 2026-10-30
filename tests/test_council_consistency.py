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
    assert list(result["synthesis_diagnostics"]["minutes_by_kind"].values()) == totals
    body = candidate_body(Candidate(**result["candidate"]))
    assert f"合计 {sum(totals)} 分钟" in body
    assert "用户陈述依据" in body and "模型假设" in body and "待确认事项" in body


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing_kind", "MISSING_STEP_KIND"),
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
    if mutation == "missing_kind":
        del candidate["steps"][0]["kind"]
    elif mutation == "missing_minutes":
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
        candidate["steps"][0]["kind"] = "rest_and_work"
    result = run_candidate(tmp_path, candidate)
    assert result["status"] == "PARTIAL" and result["candidate"] is None
    assert result["synthesis_error_code"] == code and result["calls"] == 4


def test_no_budget_still_requires_minutes_and_memory_quote_is_allowed(tmp_path):
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
        run_candidate(tmp_path, candidate, time_budget_minutes=None)["candidate"]
        is None
    )
    schema = generation_schema(
        Synthesis, {"time_budget_minutes": None, "evidence_ids": []}
    )
    step = schema["properties"]["candidate"]["properties"]["steps"]["items"]
    assert "minutes" in step["required"] and "kind" in step["required"]


def test_historical_candidate_remains_readable_without_inventing_categories():
    candidate = deepcopy(candidate_for([("work", "旧步骤", 20, "记录")]))
    del candidate["steps"][0]["kind"]
    del candidate["source_excerpts"]
    del candidate["unknowns"]
    body = candidate_body(Candidate(**candidate))
    assert "旧版候选" in body
    assert "程序汇总" not in body


@pytest.mark.parametrize("minutes", [True, "30", 1.5])
def test_minutes_are_actual_integers(tmp_path, minutes):
    candidate = candidate_for([("work", "定位", minutes, "记录")])
    result = run_candidate(tmp_path, candidate)
    assert result["candidate"] is None
    assert result["synthesis_error_code"] == "INVALID_FIELDS"
