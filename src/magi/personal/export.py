"""已保存计划的确定性文本导出，不调用模型，不修改业务记录。"""

import hashlib
import html
import re

from magi.personal.models import PlanView


def plan_markdown(plan: PlanView) -> str:
    """把数据库读取的某个版本包装成Markdown，保留原正文及版本来源。

    不添加生成时间，重复导出同一版本可核对一致性；标题转义避免变成HTML。
    正文按原样保留，此处不将其解释为执行指令或生成新的建议。
    """
    title = html.escape(" ".join(plan.title.splitlines()))
    title = re.sub(r"([\\`*_\[\]#])", r"\\\1", title)
    digest = hashlib.sha256(plan.body.encode("utf-8")).hexdigest()
    return (
        f"# {title}\n\n"
        f"版本：v{plan.version}\n\n"
        "## 已保存的计划内容\n\n"
        + plan.body
        + "\n\n---\n\n"
        + f"计划 ID：{plan.id}\n\n"
        + f"保存时间：{plan.created_at.isoformat()}\n\n"
        + f"正文 SHA-256：{digest}\n\n"
        + "这是已保存版本的副本。内容中的建议和假设仍需复核，导出不表示计划中的任务已经执行。\n"
    )
