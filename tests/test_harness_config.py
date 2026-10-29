from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from magi.harness.config import HarnessConfig, load_config


def config_file(tmp_path, text):
    (tmp_path / "docs").mkdir(exist_ok=True)
    path = tmp_path / "magi.harness.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_paths_are_relative_to_config_file_not_working_directory(tmp_path, monkeypatch):
    path = config_file(tmp_path, '[model]\nname = "another-local-model"\n')
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_config(path)

    assert config.docs_root == tmp_path / "docs"
    assert config.db_path == tmp_path / ".magi/harness.sqlite3"
    assert config.events_path == tmp_path / ".magi/harness-events.jsonl"
    assert config.model == "another-local-model"
    assert config.max_model_calls == 4
    assert not config.db_path.parent.exists()  # Loading never creates runtime files.
    with pytest.raises(FrozenInstanceError):
        config.model = "changed"


@pytest.mark.parametrize(
    "text, message",
    [
        ('[modle]\nname = "x"', "未知配置表"),
        ('[model]\nmodle = "x"', "未知字段"),
        ('[storage]\ncheckpoint = "x"', "未知字段"),
        ("[limits]\nmodel_call = 2", "未知字段"),
        ('tools = "docs"', "必须是配置表"),
        ('[model]\nprovider = "other"', "不支持的 model.provider"),
        ('[model]\nname = " "', "非空字符串"),
        ("[model]\nname = 123", "非空字符串"),
        ('[model]\nbase_url = "file:///tmp/model"', "HTTP"),
        ('[model]\nbase_url = "https://user:secret@example.test"', "无凭据"),
        ("[model]\ntimeout_seconds = inf", "有限的正数"),
        ("[model]\ntimeout_seconds = true", "有限的正数"),
        ("[model]\nnum_predict = 0", "整数"),
        ("[limits]\nmodel_calls = 0", "整数"),
        ("[limits]\nmodel_calls = true", "整数"),
        ("[limits]\ntool_calls = -1", "整数"),
        ("[limits]\ntool_calls = 2.5", "整数"),
        ('[tools]\nroot = ""', "非空字符串"),
        ('[tools]\nroot = "absent"', "已存在的目录"),
        ('[storage]\ncheckpoint_db = "docs"', "指向文件"),
        (
            '[storage]\ncheckpoint_db = "same.db"\nevent_log = "same.db"',
            "同一个文件",
        ),
        (
            '[storage]\ncheckpoint_db = "same.db"\nbusiness_db = "same.db"',
            "同一个文件",
        ),
    ],
)
def test_bad_config_fails_before_runtime(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load_config(config_file(tmp_path, text))


def test_zero_tool_budget_is_valid(tmp_path):
    config = load_config(config_file(tmp_path, "[limits]\ntool_calls = 0"))
    assert config.max_tool_calls == 0


def test_invalid_toml_is_reported_as_configuration_error(tmp_path):
    with pytest.raises(ValueError, match="合法的 UTF-8 TOML"):
        load_config(config_file(tmp_path, "[model"))


def test_direct_configuration_uses_same_validation(tmp_path):
    with pytest.raises(ValueError, match="整数"):
        HarnessConfig(
            docs_root=tmp_path,
            db_path=tmp_path / "state.db",
            events_path=tmp_path / "events.jsonl",
            max_model_calls=True,
        )


def test_absolute_paths_are_preserved(tmp_path):
    path = config_file(tmp_path, "")
    custom = tmp_path / "separate"
    custom.mkdir()
    path.write_text(f"[tools]\nroot = {str(custom.as_posix())!r}\n", encoding="utf-8")
    assert load_config(path).docs_root == Path(custom)
