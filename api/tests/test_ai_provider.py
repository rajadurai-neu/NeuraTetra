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


def test_anthropic_uses_refusal_fallback_and_foundry_does_not(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = build_reviewer(AISettings(provider="anthropic", anthropic_api_key="k", model="claude-opus-5-5"))
    f = build_reviewer(AISettings(provider="foundry", foundry_resource="r", foundry_api_key="k"))
    assert a.refusal_fallback and not f.refusal_fallback


def test_claude_reviewer_request_shape():
    import json
    from pathlib import Path
    from types import SimpleNamespace as NS

    import pytest

    from cnat_engine.ai.reviewer import AIRefusalError, AIReviewOutput
    from cnat_engine.domain import NoteContext, NoteType
    from cnat_engine.rules.ruleset import load_ruleset

    rules = load_ruleset(Path(__file__).resolve().parents[2] / "rulesets")
    note = NoteContext(note_id="n", note_type=NoteType.PROGRESS_INDIVIDUAL, text="S: a\nO: b\nA: Doing ok.\nP: c")
    out = AIReviewOutput.model_validate({"findings": [], "measure_scores": []})
    calls = {}

    def parse(name, stop="end_turn"):
        def fn(**kw):
            calls[name] = kw
            return NS(stop_reason=stop, parsed_output=out)
        return fn

    client = NS(messages=NS(parse=parse("plain")), beta=NS(messages=NS(parse=parse("beta"))))
    ClaudeReviewer(client, "claude-opus-5-5", refusal_fallback=True).review(note, rules.measures)
    assert calls["beta"]["fallbacks"] == "default" and calls["beta"]["betas"] == [ClaudeReviewer.FALLBACK_BETA]
    assert calls["beta"]["output_format"] is AIReviewOutput and "thinking" not in calls["beta"]
    ClaudeReviewer(client, "claude-opus-5-5").review(note, rules.measures)
    assert "fallbacks" not in calls["plain"] and calls["plain"]["output_config"] == {"effort": "medium"}

    refusing = NS(messages=NS(parse=parse("x", stop="refusal")))
    with pytest.raises(AIRefusalError):
        ClaudeReviewer(refusing, "claude-opus-5-5").review(note, rules.measures)
