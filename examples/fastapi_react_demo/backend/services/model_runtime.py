from typing import Any, Dict, Tuple

from openai import OpenAI


def create_model_and_config(
    api_key: str,
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> Tuple[OpenAI, Dict[str, Any]]:
    """Build OpenAI client and model_config payload for AgentController."""
    model = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    model_config = {
        "model": model_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    return model, model_config
