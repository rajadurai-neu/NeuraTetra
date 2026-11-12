"""Pick the AI reviewer from settings: Claude (Azure AI Foundry or Anthropic API), Azure OpenAI (GPT), or none."""
from __future__ import annotations

import logging
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

from cnat_api.config import PROJECT_ROOT
from cnat_engine.ai.reviewer import ClaudeReviewer, NoAIReviewer, Reviewer

log = logging.getLogger("cnat.ai")


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CNAT_AI_", env_file=PROJECT_ROOT / ".env", extra="ignore")

    provider: str = "foundry"          # foundry | anthropic | azure_openai | none
    model: str = "claude-opus-5-5"     # Foundry: your deployment name
    effort: str = "medium"
    foundry_resource: str = ""         # Azure AI Foundry resource name
    foundry_api_key: str = ""          # from the Foundry resource's Keys page
    anthropic_api_key: str = ""
    # Azure OpenAI (GPT): when Claude isn't available on the subscription. CNAT_AI_MODEL = deployment name.
    azure_openai_endpoint: str = ""    # e.g. https://<resource>.openai.azure.com
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"


def build_reviewer(s: AISettings | None = None) -> Reviewer:
    s = s or AISettings()
    if s.provider == "none":
        return NoAIReviewer()
    if s.provider == "azure_openai":
        if not (s.azure_openai_endpoint and s.azure_openai_api_key):
            log.warning("AI review OFF: CNAT_AI_AZURE_OPENAI_ENDPOINT / CNAT_AI_AZURE_OPENAI_API_KEY not set (rules only)")
            return NoAIReviewer()
        import openai

        from cnat_api.providers.azure_openai import AzureOpenAIReviewer
        client = openai.AzureOpenAI(azure_endpoint=s.azure_openai_endpoint, api_key=s.azure_openai_api_key,
                                    api_version=s.azure_openai_api_version)
        return AzureOpenAIReviewer(client, deployment=s.model)
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
