"""Pick the Claude client from settings. Azure AI Foundry in Azure; Anthropic API as a local fallback."""
from __future__ import annotations

import logging
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

from cnat_api.config import PROJECT_ROOT
from cnat_engine.ai.reviewer import ClaudeReviewer, NoAIReviewer, Reviewer

log = logging.getLogger("cnat.ai")


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
        if not (s.foundry_resource and s.foundry_api_key):
            # Not set up yet: run rules only rather than failing every assessment on a connection error.
            log.warning("AI review OFF: CNAT_AI_FOUNDRY_RESOURCE / CNAT_AI_FOUNDRY_API_KEY not set (rules only)")
            return NoAIReviewer()
        client = anthropic.AnthropicFoundry(api_key=s.foundry_api_key, resource=s.foundry_resource)
    elif s.provider == "anthropic":
        if not (s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")):
            log.warning("AI review OFF: CNAT_AI_ANTHROPIC_API_KEY not set (rules only)")
            return NoAIReviewer()
        client = anthropic.Anthropic(api_key=s.anthropic_api_key or None)
    else:
        raise ValueError(f"Unknown CNAT_AI_PROVIDER: {s.provider}")
    return ClaudeReviewer(client, model=s.model, effort=s.effort)
