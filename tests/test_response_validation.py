import json

import pytest

from magi.agent.response_validation import validate_choice, parse_and_validate_response


def test_validate_choice_valid_a():
    assert validate_choice({"choice": "A"}) == "valid"


def test_validate_choice_invalid_c():
    assert validate_choice({"choice": "C"}) == "invalid"


def test_validate_choice_missing():
    assert validate_choice({"reasoning": "证据不足"}) == "missing"


def test_parse_valid_response():
    content = (
        '{"choice": "A", '
        '"reasoning": "改动范围较小", '
        '"evidence_refs": []}'
    )

    result = parse_and_validate_response(content)

    assert result == {
        "choice": "A",
        "reasoning": "改动范围较小",
        "evidence_refs": [],
    }


@pytest.mark.parametrize("content", ['["A", "B"]', "null"])
def test_reject_non_object(content):
    with pytest.raises(ValueError, match="回复必须是 JSON 对象"):
        parse_and_validate_response(content)


def test_reject_blank_reasoning():
    content = (
        '{"choice": "A", '
        '"reasoning": "   ", '
        '"evidence_refs": []}'
    )

    with pytest.raises(ValueError, match="reasoning 必须是非空字符串"):
        parse_and_validate_response(content)
def test_reject_invalid_json():
    with pytest.raises(ValueError, match="回复不是合法 JSON"):
        parse_and_validate_response("这不是JSON")


def test_parse_valid_response_b():
    data = {
        "choice": "B",
        "reasoning": "可以复用现有实现",
        "evidence_refs": ["source-1"],
    }

    assert parse_and_validate_response(json.dumps(data)) == data


@pytest.mark.parametrize("field", [
    "choice",
    "reasoning",
    "evidence_refs",
])
def test_reject_missing_field(field):
    data = {
        "choice": "A",
        "reasoning": "改动范围较小",
        "evidence_refs": [],
    }
    del data[field]

    with pytest.raises(ValueError, match=f"缺少字段：{field}"):
        parse_and_validate_response(json.dumps(data))


@pytest.mark.parametrize(
    "field, value, expected_error",
    [
        ("choice", "C", "choice 必须是 A 或 B"),
        ("choice", 1, "choice 必须是 A 或 B"),
        ("reasoning", "", "reasoning 必须是非空字符串"),
        ("reasoning", 123, "reasoning 必须是非空字符串"),
        ("evidence_refs", "source-1", "evidence_refs 必须是列表"),
        ("evidence_refs", [123], "evidence_refs 中每项必须是非空字符串"),
        ("evidence_refs", ["   "], "evidence_refs 中每项必须是非空字符串"),
        (
            "evidence_refs",
            ["source-1", ""],
            "evidence_refs 中每项必须是非空字符串",
        ),
    ],
)
def test_reject_invalid_field(field, value, expected_error):
    data = {"choice": "A", "reasoning": "改动范围较小", "evidence_refs": [], field: value}

    with pytest.raises(ValueError, match=expected_error):
        parse_and_validate_response(json.dumps(data))