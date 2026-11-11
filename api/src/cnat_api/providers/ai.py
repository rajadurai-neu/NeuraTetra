"""Pick the Claude client from settings. Azure AI Foundry in Azure; Anthropic API as a local fallback."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from cnat_api.config import PROJECT_ROOT
from cnat_engine.ai.reviewer import ClaudeReviewer, NoAIReviewer, Reviewer


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CNAT_AI_", env_file=PROJECT_ROOT / ".env", extra="ignore")

    provider: str = "foundry"          # foundry | anthropic | none
    model: str = "claude-opus-5-5"     # Foundry: your deployment name
    effort: str = "medium"
    foundry_resource: str = ""         # Azure AI Foundry resource name
    foundry_api_key: str = ""          # from the Foundry resource's Keys page
    anthropic_api_key: str = ""


def build_reviewer(s: AISettings | None = None) -> Reviewer:
    s = s or AISettings()
    if s.provider == "none":
        return NoAIReviewer()
    import anthropic

    if s.provider == "foundry":
        client = anthropic.AnthropicFoundry(api_key=s.foundry_api_key, resource=s.foundry_resource)
    elif s.provider == "anthropic":
        client = anthropic.Anthropic(api_key=s.anthropic_api_key or None)
    else:
        raise ValueError(f"Unknown CNAT_AI_PROVIDER: {s.provider}")
    return ClaudeReviewer(client, model=s.model, effort=s.effort)
