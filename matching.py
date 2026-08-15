"""Deterministic preference filters followed by explainable LLM fit scoring."""

from __future__ import annotations

import os
from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from workflow import create_chat_models


class FitAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    decision: Literal["qualified", "rejected"]
    evidence: list[str]
    missing_requirements: list[str]
    rejection_reason: str


FIT_PROMPT = ChatPromptTemplate.from_template(
    """You are a strict job-fit evaluator. Compare only the supplied candidate facts and role.
Do not infer experience. Score 0-100. A qualified result must have score >= {threshold}.
Evidence must cite concrete alignments. Return an empty rejection_reason when qualified.

Candidate profile:
{candidate_profile}

Desired titles: {desired_titles}
Role title: {title}
Role location: {location}
Role description:
{description}
"""
)


def _values(preferences: dict[str, Any], key: str) -> list[str]:
    value = preferences.get(key, [])
    if isinstance(value, str):
        return [part.strip().lower() for part in value.split(",") if part.strip()]
    return [str(part).strip().lower() for part in value if str(part).strip()]


def hard_filter(job: dict[str, Any], preferences: dict[str, Any]) -> str | None:
    haystack = f"{job['title']} {job.get('location', '')} {job.get('employment_type', '')} {job['description']}".lower()
    excluded = _values(preferences, "excluded_keywords")
    if match := next((word for word in excluded if word in haystack), None):
        return f"Excluded keyword: {match}"

    required = _values(preferences, "required_keywords")
    if missing := next((word for word in required if word not in haystack), None):
        return f"Required keyword is missing: {missing}"

    employment = _values(preferences, "employment_types")
    if employment and not any(value in haystack for value in employment):
        return "Employment type does not match."

    seniority = _values(preferences, "seniority")
    if seniority and not any(value in job["title"].lower() for value in seniority):
        return "Seniority does not match."

    locations = _values(preferences, "locations")
    remote_policy = str(preferences.get("remote_policy", "any")).lower()
    location = job.get("location", "").lower()
    if remote_policy == "remote only" and "remote" not in haystack:
        return "Role is not remote."
    if locations and "remote" not in location and not any(value in location for value in locations):
        return "Location does not match."
    return None


class RoleMatcher:
    def __init__(self, model: Any | None = None):
        groq_name = os.getenv("GROQ_MATCH_MODEL", "openai/gpt-oss-20b")
        self.model_name = groq_name if os.getenv("GROQ_API_KEY") else os.getenv("OLLAMA_MODEL", "llama3.2")
        candidates = [model] if model is not None else create_chat_models(groq_name, temperature=0)
        structured = [
            candidate.with_structured_output(FitAssessment, method="json_schema") for candidate in candidates
        ]
        scorer = structured[0].with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
        if len(structured) > 1:
            scorer = scorer.with_fallbacks(structured[1:])
        self.chain = FIT_PROMPT | scorer

    def evaluate(self, job: dict[str, Any], profile: dict[str, Any]) -> FitAssessment:
        preferences = profile["preferences"]
        rejection = hard_filter(job, preferences)
        if rejection:
            return FitAssessment(
                score=0, decision="rejected", evidence=[], missing_requirements=[], rejection_reason=rejection
            )
        threshold = int(preferences.get("minimum_score", 70))
        result = self.chain.invoke(
            {
                "candidate_profile": profile["candidate_profile"],
                "desired_titles": ", ".join(_values(preferences, "desired_titles")) or "Any relevant role",
                "title": job["title"],
                "location": job.get("location", ""),
                "description": job["description"],
                "threshold": threshold,
            }
        )
        assessment = FitAssessment.model_validate(result)
        decision = "qualified" if assessment.score >= threshold else "rejected"
        return assessment.model_copy(update={"decision": decision})
