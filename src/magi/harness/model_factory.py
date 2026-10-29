"""Single provider boundary shared by chat and council."""

from langchain_ollama import ChatOllama

from magi.harness.config import HarnessConfig


def build_model(settings: HarnessConfig):
    """Provider-specific parameters stay at the model boundary."""
    if settings.provider != "ollama":
        raise ValueError("本轮只验证了 Ollama 提供方")
    return ChatOllama(
        model=settings.model,
        base_url=settings.base_url,
        reasoning=False,
        temperature=0,
        num_ctx=settings.num_ctx,
        num_predict=settings.num_predict,
        client_kwargs={"timeout": settings.timeout_seconds, "trust_env": False},
    )
