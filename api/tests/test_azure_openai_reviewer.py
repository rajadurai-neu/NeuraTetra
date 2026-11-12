"""Azure OpenAI (GPT) reviewer: same output contract and grounding filter as the Claude reviewer."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cnat_api.providers.ai import AISettings, build_reviewer
from cnat_api.providers.azure_openai import AzureOpenAIReviewer, output_schema
from cnat_engine.ai.reviewer import AIRefusalError, NoAIReviewer
from cnat_engine.domain import NoteContext, NoteType
from cnat_engine.rules.ruleset import load_ruleset

RULES = load_ruleset(Path(__file__).resolve().parents[2] / "rulesets")
NOTE = NoteContext(note_id="n1", note_type=NoteType.PROGRESS_INDIVIDUAL,
                   text="S: Client said things are fine.\nO:\nA: Doing ok.\nP: Continue.")


class FakeClient:
    def __init__(self, content=None, finish="stop", refusal=None):
        self.calls = []
        msg = NS(content=content, refusal=refusal)
        self.chat = NS(completions=NS(create=self._create))
        self._resp = NS(choices=[NS(message=msg, finish_reason=finish)])

    def _create(self, **kw):
        self.calls.append(kw)
        return self._resp


def payload(measure_id):
    return json.dumps({
        "findings": [
            {"rule_id": "Q-ASSESS", "element": "Assessment", "severity": "warning",
             "issue": "Assessment is vague.", "evidence_quote": "Doing ok.", "suggestion": "Describe progress toward goals."},
            {"rule_id": "Q-X", "element": "Plan", "severity": "warning",
             "issue": "Invented.", "evidence_quote": "client is suicidal", "suggestion": "n/a"},   # not in note -> dropped
        ],
        "measure_scores": [{"measure": measure_id, "score": 1, "finding": "Minimal."},
                           {"measure": "UNKNOWN", "score": 3, "finding": "ignored"}],
    })


def test_review_maps_output_and_drops_ungrounded():
    mid = RULES.measures[0].id
    client = FakeClient(payload(mid))
    r = AzureOpenAIReviewer(client, deployment="gpt-4-1").review(NOTE, RULES.measures)
    assert [f.rule_id for f in r.findings] == ["Q-ASSESS"] and r.dropped_unsupported == 1
    assert [m.measure for m in r.measure_scores] == [mid]
    assert r.model == "azure-openai:gpt-4-1"
    call = client.calls[0]
    assert call["model"] == "gpt-4-1" and call["response_format"]["type"] == "json_schema"
    assert call["messages"][0]["role"] == "system" and "Doing ok." in call["messages"][1]["content"]


def test_refusal_length_and_bad_json():
    with pytest.raises(AIRefusalError):
        AzureOpenAIReviewer(FakeClient(refusal="no"), "d").review(NOTE, RULES.measures)
    with pytest.raises(AIRefusalError):
        AzureOpenAIReviewer(FakeClient("{}", finish="content_filter"), "d").review(NOTE, RULES.measures)
    with pytest.raises(RuntimeError, match="incomplete"):
        AzureOpenAIReviewer(FakeClient("{}", finish="length"), "d").review(NOTE, RULES.measures)
    with pytest.raises(RuntimeError, match="unexpected format"):
        AzureOpenAIReviewer(FakeClient("not json"), "d").review(NOTE, RULES.measures)


def test_schema_is_strict_compatible():
    schema = output_schema()["schema"]

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    objs = list(objects(schema))
    assert objs
    for o in objs:   # OpenAI strict mode: no extra keys, every property required
        assert o.get("additionalProperties") is False
        assert set(o.get("required", [])) == set(o.get("properties", {}))


def test_provider_selection():
    assert isinstance(build_reviewer(AISettings(provider="azure_openai")), NoAIReviewer)   # not configured
    r = build_reviewer(AISettings(provider="azure_openai", azure_openai_endpoint="https://x.openai.azure.com",
                                  azure_openai_api_key="k", model="gpt-4-1"))
    assert isinstance(r, AzureOpenAIReviewer) and r.deployment == "gpt-4-1"
