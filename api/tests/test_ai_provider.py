"""AI provider selection: an unconfigured provider means rules-only, not failed assessments."""
from cnat_api.providers.ai import AISettings, build_reviewer
from cnat_engine.ai.reviewer import ClaudeReviewer, NoAIReviewer


def test_foundry_without_settings_falls_back_to_rules_only():
    assert isinstance(build_reviewer(AISettings(provider="foundry", foundry_resource="", foundry_api_key="")), NoAIReviewer)


def test_anthropic_without_key_falls_back_to_rules_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(build_reviewer(AISettings(provider="anthropic", anthropic_api_key="")), NoAIReviewer)


def test_foundry_configured_uses_claude():
    r = build_reviewer(AISettings(provider="foundry", foundry_resource="res", foundry_api_key="k"))
    assert isinstance(r, ClaudeReviewer)


def test_none():
    assert isinstance(build_reviewer(AISettings(provider="none")), NoAIReviewer)
