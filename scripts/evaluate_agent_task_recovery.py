"""在独立工作区强制结束验收子进程，核对持久任务的三个恢复分支。"""

import argparse
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from magi.harness.config import HarnessConfig
from magi.personal.locking import ApplicationLock
from magi.web.schemas import TurnInput
from magi.web.service import WebService
from magi.web.tasks import AgentTasks


def services(root, phase):
    """使用脚本模型，调用日志作为不可自动重做的动作探针。"""
    settings = HarnessConfig(
        docs_root=root,
        db_path=root / "checkpoint.db",
        events_path=root / "events.jsonl",
        business_db_path=root / "business.db",
    )

    def pause():
        (root / f"{phase}.ready").touch()
        while True:
            time.sleep(1)

    def answer(text):
        with (root / "calls.txt").open("a", encoding="utf8") as output:
            output.write(text + "\n")
        if phase == "during":
            pause()
        return f"完成：{text}"

    @contextmanager
    def factory(*_):
        yield SimpleNamespace(ask=answer)

    service = WebService(settings, "validation041", session_factory=factory)
    if phase == "after":
        original = service.turn

        def after_commit(*args, **kwargs):
            original(*args, **kwargs)
            pause()

        service.turn = after_commit
    return service, AgentTasks(service)


def kill_at(root, phase):
    """父进程等待子进程到达指定边界，再强制终止自己的子进程。"""
    with subprocess.Popen(
        [sys.executable, "-X", "utf8", __file__, "--data", str(root), "--child", phase]
    ) as process:
        try:
            deadline = time.monotonic() + 20
            marker = root / f"{phase}.ready"
            while not marker.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"{phase} 未到达验证边界")
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        return process.returncode


def main():
    """父进程生成一次验收报告，子进程只执行指定阶段。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", choices=("during", "after"))
    args = parser.parse_args()
    root = args.data.resolve()
    if args.child:
        service, tasks = services(root, args.child)
        with ApplicationLock(root / "business.lock"):
            service.recover()
            tasks.recover()
            tasks.run_one()
        return
    if not args.output or args.output.exists() or root.exists():
        parser.error("请为验收使用新的目录与报告文件")
    root.mkdir(parents=True)
    service, tasks = services(root, "parent")
    ids = {}
    for key in ("during", "after", "queued"):
        req = TurnInput(
            chat_id=key, message_id="u1", reply_message_id="a1", user_text=key
        )
        ids[key] = tasks.submit(req, "chat")["id"]

    codes = {phase: kill_at(root, phase) for phase in ("during", "after")}
    # 两次子进程结束后，使用相同数据库恢复，且最后一个任务还未被领取。
    service, tasks = services(root, "parent")
    with ApplicationLock(root / "business.lock"):
        service.recover()
        tasks.recover()
        restored = {key: tasks.get(task_id)["status"] for key, task_id in ids.items()}
        assert restored == {
            "during": "INTERRUPTED",
            "after": "COMPLETED",
            "queued": "QUEUED",
        }
        assert tasks.run_one()
        assert not tasks.run_one()
    calls = (root / "calls.txt").read_text(encoding="utf8").splitlines()
    assert calls == ["during", "after", "queued"]
    final = {key: tasks.get(task_id)["status"] for key, task_id in ids.items()}
    assert final == restored | {"queued": "COMPLETED"}
    report = {
        "exit_codes": codes,
        "after_recovery": restored,
        "final": final,
        "calls": calls,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )


if __name__ == "__main__":
    main()
