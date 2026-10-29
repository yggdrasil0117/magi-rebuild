"""Single provider boundary shared by chat and council."""

import os

from langchain_ollama import ChatOllama

from magi.harness.config import HarnessConfig


def build_model(settings: HarnessConfig):
    """Provider-specific parameters stay at the model boundary."""
    if settings.provider == "qianwen":
        import httpx
        from langchain_openai import ChatOpenAI

        key = os.environ.get(settings.api_key_env, "").strip()
        if not key:
            raise ValueError(
                f"请设置环境变量 {settings.api_key_env}，不要把密钥写进配置文件"
            )
        return ChatOpenAI(
            model=settings.model,
            base_url=settings.base_url,
            api_key=key,
            temperature=0,
            timeout=settings.timeout_seconds,
            max_retries=0,
            use_responses_api=False,
            extra_body={"enable_thinking": False, "max_tokens": settings.num_predict},
            http_client=httpx.Client(trust_env=False),
            http_async_client=httpx.AsyncClient(trust_env=False),
        )
    if settings.provider != "ollama":
        raise ValueError("不支持的模型提供方")
    return ChatOllama(
        model=settings.model,
        base_url=settings.base_url,
        reasoning=False,
        temperature=0,
        num_ctx=settings.num_ctx,
        num_predict=settings.num_predict,
        client_kwargs={"timeout": settings.timeout_seconds, "trust_env": False},
    )
