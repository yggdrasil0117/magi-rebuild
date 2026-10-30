"""确定性的可引用原文目录；编号不是新的事实或外部检索证据。"""


def source_options(snapshot):
    """从当前消息和有效记忆切出原文片段，最多24项、每项160字。

    目录有界且顺序确定；未进入目录的文字仍在快照中，但不能通过编号引用。
    不改写原文，不把历史助手意见或旧计划登记成当前用户陈述。
    """
    sources = [("user:current", snapshot.get("question", ""))]
    sources += [
        ("memory:" + m["key"], m["value"]) for m in snapshot.get("memories", [])
    ]
    options = []
    for ref, text in sources:
        if ref not in snapshot.get("evidence_ids", []):
            continue
        for start in range(0, len(text), 160):
            quote = text[start : start + 160].strip()
            if quote:
                options.append(
                    {
                        "id": f"source:{len(options)}",
                        "evidence_ref": ref,
                        "quote": quote,
                    }
                )
            if len(options) == 24:
                return options
    return options


def selected_excerpts(ids, snapshot):
    """只解析本轮目录中已有的编号；返回原文，不进行模糊匹配或猜测修复。"""
    options = {item["id"]: item for item in source_options(snapshot)}
    if len(ids) != len(set(ids)) or any(key not in options for key in ids):
        raise ValueError("unknown source selection")
    return [{k: options[key][k] for k in ("evidence_ref", "quote")} for key in ids]
