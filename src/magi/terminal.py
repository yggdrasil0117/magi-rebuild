"""MAGI 终端入口：只负责交互，模型调用、校验和真实保存仍由 HTTP 后端负责。"""

import argparse
import getpass
import itertools
import json
import os
import random
import re
import shutil
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from magi.terminal_ui import welcome_rows

# 16 个 EVA 意象 × 4 个等待动作，共 64 条；均为氛围文案，不报告运行阶段。
LOADING = tuple(
    f"{subject} {action}..."
    for subject in (
        "MELCHIOR",
        "BALTHASAR",
        "CASPER",
        "AT Field",
        "Dead Sea Scrolls",
        "NERV",
        "MAGI",
        "Entry Plug",
        "LCL",
        "GeoFront",
        "Terminal Dogma",
        "Progressive Knife",
        "Umbilical Cable",
        "Unit-01",
        "Unit-00",
        "Spear of Longinus",
    )
    for action in ("Reasoning", "Calculating", "Parsing", "Synchronizing")
)
HELP = """/chat 日常聊天   /council 三人格   /history 历史
/save 查看并确认最新候选   /details 讨论详情
/retry 重试原请求   /discard 放弃本地重试
/help 帮助   /quit 退出
直接输入文字后回车发送。多行内容请先合并为一行。本轮记忆管理及计划历史仍在网页使用。"""


def safe_text(value):
    """清除服务端文本中的终端控制字符，避免模型或历史内容控制本机终端。"""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(value))


class NoRedirect(HTTPRedirectHandler):
    """不跟随重定向，避免把本机服务凭据传到其他地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """连接用户指定的本机后端；凭据仅在内存和认证请求头中使用。"""

    def __init__(self, base, token, timeout=900):
        parsed = urlsplit(base)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("本轮终端仅连接 http://127.0.0.1:端口 等本机地址")
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, body=None):
        """发送一次请求，不自动重试；超时不能证明后端没有完成处理。"""
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(
            self.base + path,
            data=data,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            # 只输出后端约定的业务信息，不输出请求头、凭据或任意错误页面。
            try:
                detail = json.loads(exc.read())
                message = f"{detail.get('code', exc.code)}: {detail.get('message', '请求失败')}"
            except (ValueError, AttributeError):
                message = f"HTTP {exc.code}，请求失败"
            raise RuntimeError(message) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError(
                "连接中断或响应无法读取；处理结果可能已产生，请保留原请求重试"
            ) from None


class Terminal:
    """管理当前会话的展示状态；不直接访问业务数据库，也不调用模型写工具。"""

    def __init__(self, client, chat, *, color=False, animate=False, reader=input):
        self.client, self.chat, self.reader = client, chat, reader
        self.color, self.animate = color, animate
        self.mode, self.parent, self.candidate = "chat", None, None
        self.pending = None
        self.confirmation = None

    def say(self, text, color="text"):
        """输出可读文本；非交互或禁色模式不写 ANSI 颜色控制码。"""
        colors = {
            "red": "38;2;183;40;46",
            "purple": "38;2;107;63;160",
            "orange": "38;2;232;125;42",
            "text": "38;2;232;230;227",
        }
        clean = safe_text(text)
        print(
            f"\033[48;2;10;10;12;{colors[color]}m{clean}\033[0m"
            if self.color
            else clean
        )

    def waiting(self, stop):
        """仅在交互终端显示等待氛围；请求结束后停止，不推断模型真实阶段。"""
        phrase = random.choice(LOADING)
        for frame in itertools.cycle(("⬡", "⬢")):
            if stop.wait(0.5):
                break
            print(f"\r{frame} 等待响应 · {phrase}（主题动画）", end="", flush=True)

    def call(self, path, body):
        """将动效绑定一次 HTTP 请求的生命周期，异常时也清理显示。"""
        stop = threading.Event()
        worker = threading.Thread(target=self.waiting, args=(stop,), daemon=True)
        if self.animate:
            worker.start()
        try:
            return self.client.request(path, body)
        finally:
            stop.set()
            if self.animate:
                worker.join()
                print("\r" + " " * 100 + "\r", end="", flush=True)

    def welcome(self):
        """输出可信静态徽章与系统信息；非彩色终端保留相同块字符布局。"""
        print()
        for logo, info in welcome_rows(shutil.get_terminal_size((100, 30)).columns):
            if self.color:
                print("\033[38;2;183;40;46m" + logo + "\033[0m" + info)
            else:
                print(logo + info)
        print()

    def display(self, turn):
        """展示后端真实结果；候选编号与所属回复一起保留，供确认绑定检查。"""
        self.parent = turn["reply"]
        self.say("\n❯ " + turn["user_text"])
        answer = turn["text"]
        marker = "\n待确认候选（尚未保存）\n"
        if turn.get("proposal_id") and marker in answer:
            answer = answer.split(marker, 1)[1].split("\n\n候选 ID：", 1)[0]
        self.say("\n⬡ MAGI\n" + answer)
        if turn["status"] != "COMPLETED":
            self.say("本轮未完整完成：" + turn["status"])
        if turn.get("proposal_id"):
            self.candidate = turn
            self.say("⬡ 有候选建议；输入 /save 查看实际保存状态并确认。")

    def history(self, *, recent=None):
        """从共享后端恢复会话，不将模型文字当作保存结果。"""
        turns = self.client.request(f"/v1/chats/{quote(self.chat, safe='')}/turns")
        self.parent, self.candidate = None, None
        # 启动仅展示最近几轮，但回复链和最新候选仍从完整历史恢复。
        for turn in turns:
            self.parent = turn["reply"]
            if turn.get("proposal_id"):
                self.candidate = turn
        visible = turns if recent is None else turns[-recent:] if recent else []
        if recent == 0 and turns:
            self.say(f"已恢复 {len(turns)} 轮历史；/history 查看，不自动展开。")
        elif recent is not None and len(turns) > recent:
            self.say(
                f"已恢复 {len(turns)} 轮历史，显示最近 {recent} 轮；/history 查看全部。"
            )
        for turn in visible:
            self.display(turn)
        if not turns:
            self.say("当前会话还没有历史。")

    def send(self, text=None):
        """新消息生成一对请求/回复编号；失败重试必须复用原编号和原文。"""
        if self.pending is None:
            if text is None:
                self.say("没有待重试请求。")
                return
            self.pending = (
                "/v1/" + ("chat" if self.mode == "chat" else "deliberations"),
                {
                    "chat_id": self.chat,
                    "message_id": str(uuid4()),
                    "reply_message_id": str(uuid4()),
                    "parent_reply_message_id": self.parent,
                    "user_text": text,
                },
            )
        elif text is not None:
            self.say("先用 /retry 重试原消息，或 /discard 放弃本地重试。")
            return
        turn = self.call(*self.pending)
        self.pending = None
        self.display(turn)

    def save(self):
        """先展示冻结候选，用户输入确认词后才写入；失败保留原确认载荷。"""
        if not self.candidate:
            self.say("当前没有候选，请先开启 /council 生成建议。")
            return
        turn = self.candidate
        path = "/v1/proposals/" + quote(turn["proposal_id"], safe="")
        if self.confirmation is None:
            proposal = self.client.request(
                path
                + "?"
                + urlencode(
                    {
                        "chat_id": self.chat,
                        "reply_message_id": turn["reply"],
                    }
                )
            )
            if proposal.get("saved"):
                self.say(f"已实际保存为 v{proposal['saved']['result_version']}。")
                return
            self.say(
                "\n请复核本次冻结候选：\n" + proposal["title"] + "\n" + proposal["body"]
            )
            self.say("输入“确认保存”才会提交；其他输入取消。")
            if self.reader("> ").strip() != "确认保存":
                self.say("已取消，未提交保存请求。")
                return
            self.confirmation = (
                path + "/confirm",
                {
                    "chat_id": proposal["chat"],
                    "reply_message_id": proposal["reply"],
                    "payload_hash": proposal["payload_hash"],
                },
            )
        receipt = self.call(*self.confirmation)
        self.confirmation = None
        self.say(f"⬢ 后端已确认保存为 v{receipt['result_version']}。")

    def command(self, text):
        """处理用户命令；不允许新消息替换结果不确定的确认请求。"""
        if text == "/quit":
            return False
        if self.confirmation and text not in {"/save", "/help"}:
            self.say("保存结果尚未确认，请用 /save 重试同一确认；退出后可在网页核对。")
        elif text == "/help":
            self.say(HELP)
        elif self.pending and text not in {"/retry", "/discard"}:
            self.say("有结果不确定的消息；请 /retry，或 /discard 后再操作。")
        elif text in {"/chat", "/council"}:
            self.mode = text[1:]
            self.say("已切换：" + ("日常聊天" if self.mode == "chat" else "三人格审议"))
        elif text == "/history":
            self.history()
        elif text == "/save":
            self.save()
        elif text == "/details":
            if self.candidate:
                for line in self.candidate["text"].splitlines():
                    color = (
                        "orange"
                        if "BALTHASAR" in line
                        else "purple"
                        if "CASPER" in line
                        else "red"
                    )
                    self.say(line, color)
            else:
                self.say("当前没有三人格候选详情。")
        elif text == "/retry":
            self.send()
        elif text == "/discard":
            self.pending = None
            self.history()
            self.say("已放弃本地重试并读取历史；不会撤销后端已发生的处理。")
        elif text.startswith("/"):
            self.say("未知命令，请输入 /help。")
        else:
            self.send(text)
        return True


def main():
    """读取连接参数并运行交互循环；服务凭据来自环境或隐藏输入，不落盘。"""
    parser = argparse.ArgumentParser(description="MAGI NERV 终端客户端（先启动后端）")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--chat", default="study", help="与网页相同则共享该会话")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument(
        "--preview", action="store_true", help="仅预览启动界面，不连接服务"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.chat):
        parser.error("会话名仅支持 1–80 位字母、数字、下划线、连字符")
    interactive = sys.stdout.isatty()
    color = interactive and not args.no_color and "NO_COLOR" not in os.environ
    if args.preview:
        Terminal(None, args.chat, color=color).welcome()
        return 0
    try:
        token = os.environ.get("MAGI_API_TOKEN") or getpass.getpass(
            "本机服务凭据（隐藏输入）："
        )
        if not token.strip():
            raise ValueError("服务凭据不能为空")
        client = Client(args.url, token)
        terminal = Terminal(
            client,
            args.chat,
            color=color,
            animate=interactive,
        )
        terminal.welcome()
        terminal.say(f"会话：{args.chat}；与同一服务的网页共享数据。")
        terminal.history(recent=0)
        terminal.say("⬡ /council 三人格   /chat 日常   /save 保存   /help 全部命令")
        while True:
            text = input("\n⬡ 你 > ").strip()
            if not text:
                continue
            try:
                if not terminal.command(text):
                    break
            except RuntimeError as exc:
                terminal.say(str(exc))
    except (EOFError, KeyboardInterrupt):
        print("\n已退出；退出不会撤销服务端处理，重新连接后可查询历史。")
    except (ValueError, RuntimeError) as exc:
        print(safe_text(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
