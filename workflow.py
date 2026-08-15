"""LangGraph workflow for grounded cold-email generation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv


load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


DEFAULT_MODEL = "llama3.2"
DEFAULT_MAX_REVISIONS = 2


def create_chat_models(model_name: str | None = None, *, temperature: float = 0.3) -> list[Any]:
    """Return the configured hosted model followed by the local fallback."""

    models: list[Any] = []
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        from langchain_groq import ChatGroq

        models.append(
            ChatGroq(
                api_key=groq_key,
                model=model_name or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
                temperature=temperature,
            )
        )
    models.append(
        ChatOllama(
            model=os.getenv("OLLAMA_MODEL", DEFAULT_MODEL) if groq_key else (model_name or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)),
            temperature=temperature,
        )
    )
    return models


def create_chat_model(model_name: str | None = None, *, temperature: float = 0.3):
    """Return the preferred model; retained as a small public provider factory."""

    return create_chat_models(model_name, temperature=temperature)[0]


def _with_reliability(primary: Runnable[Any, Any], fallbacks: list[Runnable[Any, Any]]):
    retried = primary.with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
    return retried.with_fallbacks(fallbacks) if fallbacks else retried


class ColdEmailInput(BaseModel):
    """Validated inputs accepted by the cold-email workflow."""

    candidate_name: str
    company_name: str
    candidate_profile: str
    job_description: str
    recipient_name: str = ""
    recipient_position: str = ""
    role_title: str = ""
    applied_at: str = ""

    @field_validator("candidate_name", "company_name", "candidate_profile", "job_description")
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cannot be empty")
        return cleaned


class CandidateJobAnalysis(BaseModel):
    """Grounded context extracted before drafting the email."""

    relevant_skills: list[str] = Field(min_length=1, max_length=3)
    main_requirement: str
    strongest_alignment: str
    outreach_angle: str


class ReviewResult(BaseModel):
    """Machine-readable decision used to route the graph."""

    approved: bool
    grounded: bool
    job_title_accurate: bool
    word_count_ok: bool
    issues: list[str] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)


class ColdEmailState(TypedDict, total=False):
    candidate_name: str
    company_name: str
    candidate_profile: str
    job_description: str
    recipient_name: str
    recipient_position: str
    role_title: str
    applied_at: str
    analysis: CandidateJobAnalysis
    draft_email: str
    review: ReviewResult
    revision_count: int
    final_email: str


@dataclass(frozen=True)
class WorkflowDependencies:
    """Runnable dependencies; injectable so tests never need Ollama."""

    analysis_chain: Runnable[Any, Any]
    draft_chain: Runnable[Any, Any]
    review_chain: Runnable[Any, Any]
    revision_chain: Runnable[Any, Any]


ANALYSIS_PROMPT = ChatPromptTemplate.from_template(
    """
You are a job outreach analysis agent.

The candidate wants to cold email a recruiter or hiring manager. Analyze the
candidate profile against the job description using only the supplied text.

Candidate Profile:
{candidate_profile}

Job Description:
{job_description}

Identify up to three relevant candidate skills, the job's main requirement,
the strongest grounded alignment, and the best outreach angle. A skill that
appears only in the job description is not candidate experience.
"""
)


DRAFT_PROMPT = ChatPromptTemplate.from_template(
    """
You are a professional cold email writing agent. The email is from a job
candidate to a recruiter or hiring manager.

Candidate Name: {candidate_name}
Company Name: {company_name}
Recipient Name: {recipient_name}
Recipient Position: {recipient_position}
Application Timestamp: {applied_at}

Candidate Profile:
{candidate_profile}

Job Description:
{job_description}

Grounded Analysis:
{analysis}

Write a personalized email using only facts supported by the candidate profile
and job description.

STRICT RULES:
- Keep the complete email between 100 and 150 words.
- Use a professional, confident, and natural tone.
- Mention only candidate skills and projects stated in the candidate profile.
- Use the exact job title from the job description when one is available.
- Never invent experience duration, technologies, achievements, attachments,
  motivations, interests, business impact, or personal goals.
- Do not call the candidate seasoned, expert, or experienced unless supported.
- Avoid generic corporate language such as "drive business growth".
- Include one specific candidate project when relevant and supported.
- If a recipient name is supplied, greet that person by first name; otherwise greet the hiring team.
- State that the candidate applied only when Application Timestamp is non-empty.
- End with a polite call to action.
- Include the candidate name exactly once, in the signature.
- Do not use placeholders or Markdown code fences.

Return only the email.
"""
)


REVIEW_PROMPT = ChatPromptTemplate.from_template(
    """
You are a strict grounding and quality evaluator. Evaluate the draft without
rewriting it.

Candidate Name: {candidate_name}
Company Name: {company_name}

Candidate Profile:
{candidate_profile}

Job Description:
{job_description}

Draft Email ({word_count} words):
{draft_email}

Approve only when every candidate claim is explicitly supported, the job title
is accurate, the company spelling and capitalization are preserved, the
candidate name appears exactly once in the signature, and the complete email is
professional, grammatical, natural, and between 100 and 150 words. Treat a
skill found only in the job description as unsupported candidate experience.
List concrete issues and actionable revision instructions. If there are no
issues, return empty lists.
"""
)


REVISION_PROMPT = ChatPromptTemplate.from_template(
    """
You are revising a cold email after a strict grounding review.

Candidate Name: {candidate_name}
Company Name: {company_name}

Candidate Profile:
{candidate_profile}

Job Description:
{job_description}

Grounded Analysis:
{analysis}

Current Draft:
{draft_email}

Review Issues:
{issues}

Required Revisions:
{revision_instructions}

Rewrite the complete email and address every review instruction. Use only
supported facts, preserve the exact company name, keep the complete email
between 100 and 150 words, and include the candidate name exactly once in the
signature. Do not use placeholders or Markdown code fences.

Return only the revised email.
"""
)


def create_dependencies(model: Any | None = None) -> WorkflowDependencies:
    """Create the LangChain runnables used by graph nodes."""

    chat_models = [model] if model is not None else create_chat_models()
    chat_model = _with_reliability(chat_models[0], chat_models[1:])
    analysis_candidates = [
        candidate.with_structured_output(CandidateJobAnalysis, method="json_schema")
        for candidate in chat_models
    ]
    review_candidates = [
        candidate.with_structured_output(ReviewResult, method="json_schema")
        for candidate in chat_models
    ]
    analysis_model = _with_reliability(analysis_candidates[0], analysis_candidates[1:])
    review_model = _with_reliability(review_candidates[0], review_candidates[1:])

    return WorkflowDependencies(
        analysis_chain=ANALYSIS_PROMPT | analysis_model,
        draft_chain=DRAFT_PROMPT | chat_model,
        review_chain=REVIEW_PROMPT | review_model,
        revision_chain=REVISION_PROMPT | chat_model,
    )


def _text_content(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") if isinstance(part, dict) else str(part) for part in content]
        return "".join(parts)
    return str(content)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def clean_email(email: str) -> str:
    """Remove common model-output artifacts without changing email content."""

    cleaned_lines: list[str] = []
    previous_line: str | None = None

    for line in email.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if stripped == previous_line:
            continue
        cleaned_lines.append(line.rstrip())
        previous_line = stripped

    return "\n".join(cleaned_lines).strip()


def build_graph(
    model: Any | None = None,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
    *,
    dependencies: WorkflowDependencies | None = None,
):
    """Build and compile the bounded cold-email LangGraph workflow."""

    if max_revisions < 0:
        raise ValueError("max_revisions cannot be negative")

    chains = dependencies or create_dependencies(model)

    def validate_input(state: ColdEmailState) -> ColdEmailState:
        validated = ColdEmailInput.model_validate(state)
        return validated.model_dump()

    def analyze(state: ColdEmailState) -> ColdEmailState:
        response = chains.analysis_chain.invoke(
            {
                "candidate_profile": state["candidate_profile"],
                "job_description": state["job_description"],
            }
        )
        return {"analysis": CandidateJobAnalysis.model_validate(response)}

    def draft(state: ColdEmailState) -> ColdEmailState:
        response = chains.draft_chain.invoke(
            {
                **state,
                "analysis": state["analysis"].model_dump_json(indent=2),
            }
        )
        return {"draft_email": _text_content(response), "revision_count": 0}

    def review(state: ColdEmailState) -> ColdEmailState:
        word_count = _word_count(state["draft_email"])
        response = chains.review_chain.invoke({**state, "word_count": word_count})
        result = ReviewResult.model_validate(response)

        issues = list(result.issues)
        instructions = list(result.revision_instructions)
        deterministic_failures = False

        if not 100 <= word_count <= 150:
            deterministic_failures = True
            if "Email must contain 100 to 150 words." not in issues:
                issues.append("Email must contain 100 to 150 words.")
                instructions.append("Rewrite the complete email to contain 100 to 150 words.")

        if state["draft_email"].count(state["candidate_name"]) != 1:
            deterministic_failures = True
            if "Candidate name must appear exactly once." not in issues:
                issues.append("Candidate name must appear exactly once.")
                instructions.append("Put the candidate name exactly once, in the signature.")

        if deterministic_failures:
            result = result.model_copy(
                update={
                    "approved": False,
                    "word_count_ok": 100 <= word_count <= 150,
                    "issues": issues,
                    "revision_instructions": instructions,
                }
            )

        return {"review": result}

    def revise(state: ColdEmailState) -> ColdEmailState:
        response = chains.revision_chain.invoke(
            {
                **state,
                "analysis": state["analysis"].model_dump_json(indent=2),
                "issues": "\n".join(f"- {issue}" for issue in state["review"].issues),
                "revision_instructions": "\n".join(
                    f"- {instruction}"
                    for instruction in state["review"].revision_instructions
                ),
            }
        )
        return {
            "draft_email": _text_content(response),
            "revision_count": state["revision_count"] + 1,
        }

    def route_after_review(state: ColdEmailState) -> Literal["revise", "finalize"]:
        if state["review"].approved or state["revision_count"] >= max_revisions:
            return "finalize"
        return "revise"

    def finalize(state: ColdEmailState) -> ColdEmailState:
        return {"final_email": clean_email(state["draft_email"])}

    builder = StateGraph(ColdEmailState)
    builder.add_node("validate", validate_input)
    builder.add_node("analyze", analyze)
    builder.add_node("draft", draft)
    builder.add_node("review", review)
    builder.add_node("revise", revise)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "validate")
    builder.add_edge("validate", "analyze")
    builder.add_edge("analyze", "draft")
    builder.add_edge("draft", "review")
    builder.add_conditional_edges("review", route_after_review)
    builder.add_edge("revise", "review")
    builder.add_edge("finalize", END)

    return builder.compile()


def generate_cold_email(
    input_data: ColdEmailInput | Mapping[str, str],
    graph: Any | None = None,
) -> str:
    """Validate input, invoke the workflow, and return only the final email."""

    validated = (
        input_data
        if isinstance(input_data, ColdEmailInput)
        else ColdEmailInput.model_validate(input_data)
    )
    workflow = graph or build_graph()
    result = workflow.invoke(validated.model_dump())
    return result["final_email"]
