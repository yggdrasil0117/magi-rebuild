"""脚本模型通过真实Agent图搜索资料，区分工具结果与模型表述。"""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from magi.harness.runtime import build_read_tools, open_session
from tests.test_harness_runtime import ScriptedModel, calls
from tests.test_harness_runtime import settings as settings


def test_real_graph_receives_search_quote_and_location(settings):
    note = settings.docs_root / "guide.md"
    note.write_text("标题\n计划导出只生成文件，不代表任务完成。\n", encoding="utf-8")
    model = ScriptedModel(
        responses=[
            calls(("magi_search_files", {"query": "计划导出", "path": "."})),
            AIMessage(content="导出不代表完成，来源guide.md第2行。"),
        ]
    )
    with open_session(settings, "search", model=model) as session:
        answer = session.ask("查找计划导出资料")
        result = json.loads(
            next(m.content for m in model.requests[-1] if isinstance(m, ToolMessage))
        )
        assert result["matches"][0]["path"] == "guide.md"
        assert result["matches"][0]["line"] == 2
        assert result["matches"][0]["quote"] == "计划导出只生成文件，不代表任务完成。"
        assert result["complete"] and "guide.md" in answer
        assert any(
            e["type"] == "tool_succeeded" and e["name"] == "magi_search_files"
            for e in session.events.records
        )
    assert note.read_text(encoding="utf-8").startswith("标题")


def test_graph_reports_no_results_without_fabricated_sources(settings):
    model = ScriptedModel(
        responses=[
            calls(("magi_search_files", {"query": "unique-missing-token"})),
            AIMessage(content="本次搜索没有找到相关片段，可以换关键词。"),
        ]
    )
    with open_session(settings, "search-none", model=model) as session:
        session.ask("查找不存在的词")
    result = json.loads(
        next(m.content for m in model.requests[-1] if isinstance(m, ToolMessage))
    )
    assert result["matches"] == [] and result["complete"]


@pytest.mark.parametrize(
    "extra", [{"limit": 999}, {"root": "D:/"}, {"confirmed": True}]
)
def test_model_cannot_change_search_scope_or_budgets(settings, extra):
    tool = next(
        t for t in build_read_tools(settings.docs_root) if t.name == "magi_search_files"
    )
    with pytest.raises(ValidationError):
        tool.invoke({"query": "test", **extra})
