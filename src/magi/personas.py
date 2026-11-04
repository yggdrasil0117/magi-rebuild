"""MAGI 三人格的稳定身份与首阶段产品提示配置。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaConfig:
    """人格配置可版本化；agent_id 是持久身份，展示名和提示词可演进。"""

    agent_id: str
    display_name: str
    config_version: str
    focus: str


PERSONAS = {
    "melchior": PersonaConfig(
        "melchior",
        "MELCHIOR",
        "research-v1",
        "优先检查证据、因果和可验证性；明确区分实测、来源陈述与推测。",
    ),
    "balthasar": PersonaConfig(
        "balthasar",
        "BALTHASAR",
        "research-v1",
        "优先检查维护负担、用户精力、恢复成本以及失败后的实际影响。",
    ),
    "casper": PersonaConfig(
        "casper",
        "CASPER",
        "research-v1",
        "优先检查个人意愿和选择，避免把主流方案或模型推测当成用户偏好。",
    ),
}


def get_persona(agent_id: str) -> PersonaConfig:
    """按稳定 ID 返回内置人格；拒绝让请求临时创建未审计角色。"""

    try:
        return PERSONAS[agent_id]
    except KeyError as exc:
        raise ValueError(f"未知人格：{agent_id}") from exc
