"""固定三组跨进程审批兼容性验收；使用独立合成资料与离线脚本模型。"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path


def main():
    """保存全部命令结果与预期拒绝，成功与失败均不重试或覆盖旧记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "mode": "scripted model; real Deep Agents + SQLite + filesystem; separate process per command",
        "versions": {
            name: version(name)
            for name in (
                "deepagents",
                "langgraph",
                "langchain",
                "langgraph-checkpoint-sqlite",
            )
        },
        "commands": [],
        "cases": [],
    }

    def save():
        """每个命令后立即保存，不丢弃中途失败记录。"""
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def run(root, action, expected_code=0, **fields):
        """每次启动新进程；stdout为结构化安全结果，记录实际退出状态。"""
        command = [
            sys.executable,
            str(Path(__file__).with_name("probe_approval.py")),
            action,
            "--data-dir",
            str(root),
        ]
        for key, value in fields.items():
            command += ["--" + key.replace("_", "-"), value]
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8"
        )
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            output = {"unparsed_stdout": result.stdout, "stderr": result.stderr}
        report["commands"].append(
            {
                "case": root.name,
                "action": action,
                "returncode": result.returncode,
                "expected_returncode": expected_code,
                "result": output,
            }
        )
        save()
        if result.returncode != expected_code:
            raise RuntimeError("验收命令退出状态不符合预期，请查看保留的证据")
        return output

    with tempfile.TemporaryDirectory(prefix="magi-approval-") as temporary:
        for name in ["approve", "reject", "stale_and_duplicate"]:
            root = Path(temporary) / name
            saved = run(root, "init")
            pending = run(root, "start", document_id=saved["id"])
            assert (
                pending["phase"] == "PENDING" and not (root / "task-documents").exists()
            )
            restored = run(root, "status", document_id=saved["id"])
            assert restored == pending
            if name == "stale_and_duplicate":
                wrong = run(
                    root,
                    "approve",
                    expected_code=1,
                    document_id=saved["id"],
                    interrupt_id="stale",
                    payload_hash=saved["payload_hash"],
                )
                assert wrong["error_code"] == "STALE_INTERRUPT"
                assert run(root, "status", document_id=saved["id"]) == pending
            decision = "reject" if name == "reject" else "approve"
            final = run(
                root,
                decision,
                document_id=saved["id"],
                interrupt_id=pending["interrupt_id"],
                payload_hash=saved["payload_hash"],
            )
            assert final["phase"] == "COMPLETED"
            files = list((root / "task-documents").glob("*.md"))
            assert len(files) == (0 if decision == "reject" else 1)
            if name == "stale_and_duplicate":
                duplicate = run(
                    root,
                    "approve",
                    expected_code=1,
                    document_id=saved["id"],
                    interrupt_id=pending["interrupt_id"],
                    payload_hash=saved["payload_hash"],
                )
                assert duplicate["error_code"] == "NO_PENDING_APPROVAL"
                assert len(list((root / "task-documents").glob("*.md"))) == 1
            checksum = (
                hashlib.sha256(files[0].read_bytes()).hexdigest() if files else None
            )
            assert checksum is None or checksum == saved["payload_hash"]
            report["cases"].append(
                {
                    "case": name,
                    "pending_restored_in_new_process": True,
                    "status": final,
                    "file_count": len(files),
                    "sha256": checksum,
                    "passed": True,
                }
            )
            save()
    report["source_sha256"] = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in (
            "src/magi/harness/runtime.py",
            "src/magi/harness/approval_trial.py",
            "scripts/probe_approval.py",
            "scripts/evaluate_approval.py",
        )
    }
    save()
    print(
        json.dumps(
            {
                "cases": len(report["cases"]),
                "commands": len(report["commands"]),
                "passed": all(c["passed"] for c in report["cases"]),
            }
        )
    )


if __name__ == "__main__":
    main()
