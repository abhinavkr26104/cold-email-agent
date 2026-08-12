from collections.abc import Iterator

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from workflow import (
    CandidateJobAnalysis,
    ReviewResult,
    WorkflowDependencies,
    build_graph,
    clean_email,
    generate_cold_email,
)


INPUT = {
    "candidate_name": "Ada Lovelace",
    "company_name": "Analytical Engines Ltd",
    "candidate_profile": "Python developer who built a scheduling application.",
    "job_description": "Software Engineer role requiring Python application development.",
}


def valid_email(marker: str = "grounded") -> str:
    body = " ".join([marker] * 105)
    return f"Subject: Software Engineer application\n\n{body}\n\nRegards,\nAda Lovelace"


def review_result(approved: bool) -> ReviewResult:
    return ReviewResult(
        approved=approved,
        grounded=approved,
        job_title_accurate=True,
        word_count_ok=True,
        issues=[] if approved else ["The draft needs revision."],
        revision_instructions=[] if approved else ["Correct the unsupported claim."],
    )


def dependencies(
    reviews: list[ReviewResult],
    *,
    draft_email: str | None = None,
    revised_emails: Iterator[str] | None = None,
    calls: dict[str, int] | None = None,
) -> WorkflowDependencies:
    counters = calls if calls is not None else {}
    remaining_reviews = iter(reviews)
    last_review = reviews[-1]

    def analyze(_):
        return CandidateJobAnalysis(
            relevant_skills=["Python"],
            main_requirement="Build Python applications",
            strongest_alignment="The candidate has built a Python application",
            outreach_angle="Lead with the scheduling application",
        )

    def review(_):
        counters["review"] = counters.get("review", 0) + 1
        return next(remaining_reviews, last_review)

    def revise(_):
        counters["revise"] = counters.get("revise", 0) + 1
        if revised_emails is None:
            return valid_email("revised")
        return next(revised_emails)

    return WorkflowDependencies(
        analysis_chain=RunnableLambda(analyze),
        draft_chain=RunnableLambda(lambda _: draft_email or valid_email()),
        review_chain=RunnableLambda(review),
        revision_chain=RunnableLambda(revise),
    )


def test_approved_first_draft_skips_revision():
    calls: dict[str, int] = {}
    graph = build_graph(
        dependencies=dependencies([review_result(True)], calls=calls)
    )

    result = generate_cold_email(INPUT, graph=graph)

    assert result == valid_email()
    assert calls == {"review": 1}


def test_failed_review_routes_to_revision_then_approval():
    calls: dict[str, int] = {}
    revised = valid_email("corrected")
    graph = build_graph(
        dependencies=dependencies(
            [review_result(False), review_result(True)],
            revised_emails=iter([revised]),
            calls=calls,
        )
    )

    result = generate_cold_email(INPUT, graph=graph)

    assert result == revised
    assert calls == {"review": 2, "revise": 1}


def test_revision_loop_stops_at_configured_limit():
    calls: dict[str, int] = {}
    graph = build_graph(
        max_revisions=2,
        dependencies=dependencies([review_result(False)], calls=calls),
    )

    result = generate_cold_email(INPUT, graph=graph)

    assert result == valid_email("revised")
    assert calls == {"review": 3, "revise": 2}


def test_deterministic_checks_override_an_incorrect_approval():
    calls: dict[str, int] = {}
    too_short = "Subject: Role\n\nHello.\n\nRegards,\nAda Lovelace"
    graph = build_graph(
        max_revisions=1,
        dependencies=dependencies(
            [review_result(True), review_result(True)],
            draft_email=too_short,
            calls=calls,
        ),
    )

    result = generate_cold_email(INPUT, graph=graph)

    assert result == valid_email("revised")
    assert calls == {"review": 2, "revise": 1}


@pytest.mark.parametrize(
    "field",
    ["candidate_name", "company_name", "candidate_profile", "job_description"],
)
def test_blank_inputs_are_rejected_before_graph_invocation(field):
    invalid_input = {**INPUT, field: "   "}

    with pytest.raises(ValidationError):
        generate_cold_email(invalid_input)


def test_clean_email_removes_fences_and_adjacent_duplicate_lines():
    email = "```email\nSubject: Role\nSubject: Role\n\n\nBody\n```"

    assert clean_email(email) == "Subject: Role\n\nBody"


def test_negative_revision_limit_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        build_graph(max_revisions=-1, dependencies=dependencies([review_result(True)]))
