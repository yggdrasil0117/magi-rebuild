"""NERV 终端视觉素材；仅负责展示，不改变模型、候选或保存行为。

徽章块字符来自 Ax1i1om/NERV-CODE 的 Clawd.tsx。
Copyright (c) 2026 NERV-CODE Contributors，MIT；完整声明见 THIRD_PARTY_NOTICES.md。
"""

NERV_BLOCKS = (
    "                   ▄▄▄",
    "                   ▄▄███████",
    "            ▄     ▄████████   ▄",
    "           ▀█▄    ████████▀▄███████",
    "             ▀█▄ ▄███████████████████▄",
    "               ▀███████████████████████",
    "                 ▀███████████████████▀",
    "        ███  ▀█ ███▀█████████████▀▀",
    "        █▀██▄ █ ███▄█▀█████████▄▄",
    "        █  ▀███ ███ ▀  ███████████▄",
    "       ▄█▄   ▀█ ███▄▄██ ▀██████████",
    "     █          ███▀██████▀█████████",
    "     ▀█         ███ ███ ▀██▄▀██████▄",
    "      ██        ██████▄   ▀███▀█████▄",
    "       ██       ███ ▀███    ▀█  ▀███",
    "        ▀█▄                       ██",
    "          ▀█▄                   ▄▄█▀",
    "            ▀▀█▄▄▄▄        ▄▄▄▀▀▀",
    "                 ▀▀▀▀▀▀▀▀▀▀",
)


def nerv_emblem_svg():
    """把终端徽章的半块字符渲染为网页矢量图，两个界面共用一份素材。"""
    path = []
    for y, row in enumerate(NERV_BLOCKS):
        for x, char in enumerate(row):
            if char in "█▀":
                path.append(f"M{x} {y * 2}h1v1h-1z")
            if char in "█▄":
                path.append(f"M{x} {y * 2 + 1}h1v1h-1z")
    width = max(map(len, NERV_BLOCKS)) - 5
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="5 0 {width} {len(NERV_BLOCKS) * 2}">'
        f'<path fill="#f04639" d="{"".join(path)}"/></svg>'
    )


def welcome_rows(width=100):
    """按窗口列数安排徽章和信息；返回两列文本，颜色由终端入口统一应用。

    宽屏并排，普通窗口上下排列，极窄窗口使用文字标识，避免徽章折行变形。
    信息仅说明产品身份，不虚构模型名称、连接成功或正在运行的 Agent。
    """
    info = (
        "MAGI SYSTEM  v0.1.0",
        "NERV / Personal Decision Terminal",
        "MELCHIOR · BALTHASAR · CASPER",
        "God's in his heaven.",
        "All's right with the world.",
    )
    logo_width = max(map(len, NERV_BLOCKS))
    if width >= logo_width + 5 + max(map(len, info)):
        return [
            (line.ljust(logo_width) + "     ", info[i - 7] if 7 <= i < 12 else "")
            for i, line in enumerate(NERV_BLOCKS)
        ]
    if width >= logo_width:
        return (
            [(line, "") for line in NERV_BLOCKS]
            + [("", "")]
            + [("", line) for line in info]
        )
    return [("NERV / MAGI", ""), ("", "PERSONAL TERMINAL")]
