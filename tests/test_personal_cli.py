import hashlib
import json
import subprocess
import sys


def run_cli(root, *arguments):
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "magi.personal.cli", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


def test_cli_persists_across_processes_and_queries_receipt(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "magi.toml"
    config.write_text(
        "[tools]\n"
        f"root = {str(docs.as_posix())!r}\n"
        "[storage]\n"
        "business_db = 'state/personal.sqlite3'\n",
        encoding="utf-8",
    )
    root = tmp_path
    common = ("--config", str(config))
    added = run_cli(
        root,
        *common,
        "memory",
        "add",
        "--key",
        "style",
        "--scope",
        "global",
        "--value",
        "conclusion first",
        "--request-id",
        "cli-1",
    )
    assert added.returncode == 0, added.stderr
    receipt = json.loads(added.stdout)

    listed = run_cli(root, *common, "memory", "list")
    assert listed.returncode == 0, listed.stderr
    memories = json.loads(listed.stdout)
    assert memories[0]["id"] == receipt["object_id"]
    assert memories[0]["value"] == "conclusion first"

    operation = run_cli(root, *common, "operation", "show", "--request-id", "cli-1")
    assert operation.returncode == 0, operation.stderr
    assert json.loads(operation.stdout)["result_id"] == receipt["object_id"]


def test_cli_reports_stable_business_error(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "magi.toml"
    config.write_text(f"[tools]\nroot = {str(docs.as_posix())!r}\n", encoding="utf-8")
    result = run_cli(
        tmp_path,
        "--config",
        str(config),
        "memory",
        "add",
        "--key",
        "style",
        "--scope",
        "invalid",
        "--value",
        "x",
        "--request-id",
        "bad",
    )
    assert result.returncode == 2
    assert "INVALID_INPUT" in result.stderr


def test_plan_cli_accepts_hash_bound_json_and_persists_versions(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "magi.toml"
    config.write_text(f"[tools]\nroot = {str(docs.as_posix())!r}\n", encoding="utf-8")
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps({"title": "Week", "body": "Task A", "sources": ["notes"]}),
        encoding="utf-8",
    )
    digest = hashlib.sha256(plan_file.read_bytes()).hexdigest()
    created = run_cli(
        tmp_path,
        "--config",
        str(config),
        "plan",
        "create",
        "--input",
        str(plan_file),
        "--content-sha256",
        digest,
        "--request-id",
        "plan-1",
    )
    assert created.returncode == 0, created.stderr
    receipt = json.loads(created.stdout)

    updated = run_cli(
        tmp_path,
        "--config",
        str(config),
        "plan",
        "update",
        "--id",
        receipt["object_id"],
        "--expected-version",
        "1",
        "--title",
        "Week",
        "--body",
        "Task A and B",
        "--request-id",
        "plan-2",
    )
    assert updated.returncode == 0, updated.stderr
    current = run_cli(
        tmp_path,
        "--config",
        str(config),
        "plan",
        "show",
        "--id",
        receipt["object_id"],
    )
    assert json.loads(current.stdout)["body"] == "Task A and B"
    old = run_cli(
        tmp_path,
        "--config",
        str(config),
        "plan",
        "show",
        "--id",
        receipt["object_id"],
        "--version",
        "1",
    )
    assert json.loads(old.stdout)["body"] == "Task A"


def test_plan_input_digest_mismatch_does_not_write(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "magi.toml"
    config.write_text(f"[tools]\nroot = {str(docs.as_posix())!r}\n", encoding="utf-8")
    plan_file = tmp_path / "plan.json"
    plan_file.write_text('{"title":"x","body":"y"}', encoding="utf-8")
    result = run_cli(
        tmp_path,
        "--config",
        str(config),
        "plan",
        "create",
        "--input",
        str(plan_file),
        "--content-sha256",
        "0" * 64,
        "--request-id",
        "bad-plan",
    )
    assert result.returncode == 1
    assert "does not match" in result.stderr
