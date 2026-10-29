"""Run with python -m magi.harness.cli from the repository root."""

import argparse
import sys
from pathlib import Path
from uuid import uuid4

from magi.harness.config import load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description="MAGI 持久会话与只读工具试验")
    parser.add_argument("--config", type=Path, default=Path("magi.harness.toml"))
    parser.add_argument("--session", default=None, help="沿用此 ID 恢复同一会话")
    parser.add_argument("--message", help="发送一条消息并退出；不传则进入交互模式")
    parser.add_argument(
        "--history", action="store_true", help="显示持久历史，不请求模型"
    )
    args = parser.parse_args(argv)
    if args.history and (not args.session or args.message is not None):
        parser.error("--history 需要 --session，并且不能与 --message 同用")
    try:
        from magi.harness.runtime import open_personal_session, open_session
        from magi.personal.context import ContextService
        from magi.personal.locking import ApplicationLock
        from magi.personal.store import PersonalStore
    except ModuleNotFoundError as exc:
        print(
            f"缺少试验依赖：{exc.name}。请使用 .venv-harness 并安装 .[harness,dev]。",
            file=sys.stderr,
        )
        return 2

    session_id = args.session or uuid4().hex
    try:
        settings = load_config(args.config)
        with ApplicationLock(settings.business_db_path.with_suffix(".lock")):
            if args.history:
                context = ContextService(PersonalStore(settings.business_db_path))
                current = context.resolve_binding(session_id)
                segments = [("legacy", session_id)] + [
                    (f"epoch {item.epoch}", item.thread_id)
                    for item in context.bindings(session_id)
                ]
                found = False
                seen = set()
                for label, thread_id in segments:
                    if thread_id in seen:
                        continue
                    seen.add(thread_id)
                    with open_session(settings, thread_id) as segment:
                        messages = [
                            message
                            for message in segment.messages()
                            if message.type in {"human", "ai"} and message.text
                        ]
                    if not messages:
                        continue
                    found = True
                    suffix = (
                        "（当前）" if thread_id == current.thread_id else "（历史）"
                    )
                    print(f"--- {label} {suffix} ---")
                    for message in messages:
                        print(f"[{message.type}] {message.text}")
                if not found:
                    print("没有已保存的消息。")
                return 0
            with open_personal_session(settings, session_id) as session:
                return _run_chat(args, session_id, session, settings)
    except KeyboardInterrupt:
        print("\n已中断。未完成的执行不会自动重放。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"本次执行失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 1


def _run_chat(args, session_id, session, settings):
    print(f"会话 ID：{session_id}（当前记忆 epoch {session.epoch}）")
    if args.message is not None:
        print(session.ask(args.message))
    else:
        print("输入 /exit 退出；重启时使用相同 --session 恢复。")
        while True:
            try:
                text = input("你：")
            except EOFError:
                break
            if text.strip() == "/exit":
                break
            if text.strip():
                print(f"MAGI：{session.ask(text)}")
    print(f"执行事件：{settings.events_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
