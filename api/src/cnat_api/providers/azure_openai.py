"""AI review with an Azure OpenAI (GPT) deployment, for subscriptions where Claude isn't available.

Same contract as the Claude reviewer: same prompt, same fixed output structure (strict JSON schema),
same grounding filter (findings must quote the note exactly), so results are stored and shown the same way.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from cnat_engine.ai.reviewer import (
    PROMPT_FILE, PROMPT_VERSION, AIRefusalError, AIReviewOutput, ReviewResult, build_user_message,
    to_review_result,
)
from cnat_engine.domain import NoteContext
from cnat_engine.rules.ruleset import QualityMeasure


def output_schema() -> dict:
    """AIReviewOutput as an OpenAI strict JSON schema (every field required, no extra keys)."""
    return {"name": "note_review", "strict": True, "schema": AIReviewOutput.model_json_schema()}


class AzureOpenAIReviewer:
    """`client` is openai.AzureOpenAI(...). `deployment` is the deployment name in Azure."""

    def __init__(self, client, deployment: str, max_output_tokens: int = 16000):
        self.client = client
        self.deployment = deployment
        self.model = f"azure-openai:{deployment}"   # kept distinct in stored results and the AI cache
        self.prompt_version = PROMPT_VERSION
        self.system_prompt = PROMPT_FILE.read_text()
        self.max_output_tokens = max_output_tokens

    def review(self, note: NoteContext, measures: list[QualityMeasure]) -> ReviewResult:
        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": build_user_message(note, measures)},
            ],
            response_format={"type": "json_schema", "json_schema": output_schema()},
            max_completion_tokens=self.max_output_tokens,
            seed=7,   # best-effort repeatability; stored results are reused for unchanged notes anyway
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise AIRefusalError("The model declined to review this note.")
        if choice.finish_reason == "length":
            raise RuntimeError("AI review incomplete (output limit reached).")
        if choice.finish_reason == "content_filter":
            raise AIRefusalError("Azure content filter blocked the review of this note.")
        try:
            out = AIReviewOutput.model_validate(json.loads(choice.message.content or ""))
        except (json.JSONDecodeError, ValidationError) as e:
            raise RuntimeError(f"AI review returned an unexpected format: {str(e)[:200]}") from e
        return to_review_result(out, note, measures, self.model)
