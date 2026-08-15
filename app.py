"""Streamlit interface for one-shot generation and automated job outreach."""

from __future__ import annotations

import html
import json
import os

import streamlit as st
from pydantic import ValidationError

from automation import (
    DAILY_SEND_LIMIT,
    MONTHLY_HUNTER_LIMIT,
    enrich_applied_jobs,
    prepare_queue,
    run_discovery,
    send_approved,
    sync_replies,
)
from discovery import infer_source
from document_input import DocumentInputError, resolve_document_text
from gmail_provider import GmailProvider
from storage import Database
from workflow import ColdEmailInput, build_graph, generate_cold_email


st.set_page_config(page_title="Scoutly · Job Outreach Agent", page_icon="✦", layout="wide")

st.markdown(
    """
    <style>
    :root {--ink:#e8eef8;--muted:#8b9bb4;--line:rgba(148,163,184,.15);--cyan:#62e6d2;}
    .stApp {background:radial-gradient(circle at 72% -10%,rgba(76,69,180,.22),transparent 36rem),radial-gradient(circle at 10% 30%,rgba(20,184,166,.08),transparent 30rem),#080d18;color:var(--ink);}
    .block-container {padding:2.4rem 2.4rem 5rem;max-width:1380px;}
    [data-testid="stSidebar"] {border-right:1px solid var(--line);background:rgba(7,12,23,.96);}
    [data-testid="stSidebar"] .block-container {padding:1.8rem 1.2rem;}
    [data-testid="stSidebar"] [data-baseweb="select"] > div {border-radius:12px;border-color:var(--line);background:rgba(255,255,255,.04);}
    .brand {display:flex;align-items:center;gap:.8rem;margin:.1rem 0 1.7rem;}
    .brand-mark {display:grid;place-items:center;width:42px;height:42px;border-radius:13px;color:#08111c;font-size:1.25rem;font-weight:900;background:linear-gradient(135deg,var(--cyan),#7aa2ff);box-shadow:0 10px 30px rgba(98,230,210,.2);}
    .brand-name {font-size:1.08rem;font-weight:750;letter-spacing:-.02em;}.brand-note{font-size:.75rem;color:var(--muted);margin-top:.08rem;}
    .hero {position:relative;overflow:hidden;padding:3.2rem 3.1rem;border-radius:28px;margin-bottom:1.5rem;border:1px solid rgba(139,155,255,.2);background:linear-gradient(125deg,rgba(24,37,65,.96),rgba(29,33,74,.92) 55%,rgba(9,72,75,.82));box-shadow:0 24px 80px rgba(0,0,0,.28);}
    .hero:after {content:"";position:absolute;width:260px;height:260px;border-radius:50%;right:-60px;top:-100px;border:45px solid rgba(98,230,210,.08);}
    .eyebrow {color:var(--cyan);font-size:.74rem;text-transform:uppercase;letter-spacing:.16em;font-weight:800;margin-bottom:.8rem;}
    .hero h1 {font-size:clamp(2.3rem,5vw,4rem);line-height:1.02;letter-spacing:-.055em;margin:0 0 .8rem;color:white;max-width:820px;}
    .hero p {font-size:1.08rem;line-height:1.65;margin:0;color:#b9c6da;max-width:760px;}
    .hero-badges {display:flex;flex-wrap:wrap;gap:.55rem;margin-top:1.6rem;}.hero-badge{padding:.45rem .72rem;border:1px solid rgba(255,255,255,.13);border-radius:999px;background:rgba(255,255,255,.055);color:#d7e2f1;font-size:.78rem;}
    .page-heading {margin:.2rem 0 1.7rem;}.page-heading h1{font-size:2.35rem;letter-spacing:-.045em;margin:.2rem 0 .45rem;color:#f4f7fb;}.page-heading p{color:var(--muted);max-width:760px;line-height:1.6;margin:0;}
    [data-testid="stMetric"] {padding:1.05rem 1.15rem;border-radius:16px;border:1px solid var(--line);background:linear-gradient(145deg,rgba(255,255,255,.055),rgba(255,255,255,.018));}
    [data-testid="stMetricLabel"]{color:var(--muted);}[data-testid="stMetricValue"]{letter-spacing:-.045em;}
    .step-card {min-height:150px;padding:1.25rem;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(255,255,255,.052),rgba(255,255,255,.018));}
    .step-number {color:var(--cyan);font-size:.72rem;letter-spacing:.12em;font-weight:800;}.step-card h3{margin:.55rem 0 .45rem;font-size:1.02rem;color:#eef4fc;}.step-card p{margin:0;color:var(--muted);font-size:.88rem;line-height:1.5;}
    .empty-state{text-align:center;padding:3.2rem 1.5rem;border:1px dashed rgba(148,163,184,.22);border-radius:20px;background:rgba(255,255,255,.018);color:var(--muted);}.empty-icon{font-size:1.8rem;color:var(--cyan);margin-bottom:.7rem;}.empty-state h3{color:#e9f0fa;margin:.2rem 0 .4rem;font-size:1.05rem;}
    div.stButton > button,div.stLinkButton > a{border-radius:11px;font-weight:700;}div[data-testid="stForm"]{border:1px solid var(--line);border-radius:20px;background:rgba(11,19,34,.6);padding:1.25rem;}div[data-testid="stExpander"]{border:1px solid var(--line);border-radius:15px;background:rgba(12,21,38,.62);overflow:hidden;}
    .connection-strip{display:flex;gap:.5rem;flex-wrap:wrap;margin:.75rem 0 1.1rem;}.connection-pill{display:inline-flex;align-items:center;gap:.42rem;padding:.4rem .65rem;border:1px solid var(--line);border-radius:999px;color:#b9c6d8;font-size:.76rem;}.connection-dot{width:7px;height:7px;border-radius:50%;background:var(--cyan);box-shadow:0 0 10px rgba(98,230,210,.7);}hr{border-color:var(--line)!important;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_graph():
    return build_graph()


@st.cache_resource
def get_database():
    return Database()


def comma_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def navigate_to(page: str) -> None:
    st.session_state["navigation"] = page


def page_header(eyebrow: str, title: str, description: str) -> None:
    st.markdown(
        f"""<section class="page-heading"><div class="eyebrow">{html.escape(eyebrow)}</div>
        <h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></section>""",
        unsafe_allow_html=True,
    )


def empty_state(title: str, description: str, icon: str = "✦") -> None:
    st.markdown(
        f"""<div class="empty-state"><div class="empty-icon">{icon}</div>
        <h3>{html.escape(title)}</h3><div>{html.escape(description)}</div></div>""",
        unsafe_allow_html=True,
    )


def decoded_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(item) for item in parsed]
    except (json.JSONDecodeError, TypeError):
        return []


def render_dashboard(db: Database) -> None:
    st.markdown(
        """
        <section class="hero">
            <div class="eyebrow">Your private job-search copilot</div>
            <h1>Turn promising roles into real conversations.</h1>
            <p>Scout relevant openings, understand why they fit, and prepare thoughtful recruiter
            outreach—while you stay in control of every application and send.</p>
            <div class="hero-badges">
                <span class="hero-badge">✦ Explainable matching</span>
                <span class="hero-badge">✓ Human-approved sends</span>
                <span class="hero-badge">↗ Reply tracking</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    stats = db.dashboard_stats()
    columns = st.columns(6)
    columns[0].metric("Open roles", stats["open_roles"])
    columns[1].metric("Qualified", stats["qualified"])
    columns[2].metric("Applied", stats["applied"])
    columns[3].metric("Awaiting approval", stats["queued"])
    columns[4].metric("Sent", stats["sent"])
    columns[5].metric("Human replies", stats["replies"])

    profile = db.get_profile()
    sources = db.list_sources()
    groq_ready = bool(os.getenv("GROQ_API_KEY"))
    st.subheader("Workspace readiness")
    status_columns = st.columns(4)
    if profile:
        status_columns[0].success("Profile ready")
    else:
        status_columns[0].warning("Add your candidate profile")
    if sources:
        status_columns[1].success(f"{len(sources)} career board(s)")
    else:
        status_columns[1].warning("Add company career boards")
    if groq_ready:
        status_columns[2].success("Groq connected")
    else:
        status_columns[2].info("Using local Ollama")
    if os.getenv("JOOBLE_API_KEY"):
        status_columns[3].success("Automatic job search ready")
    else:
        status_columns[3].warning("Add a Jooble API key")

    last_search = db.last_run("discover")
    hunter_used = db.hunter_usage()
    detail_columns = st.columns(2)
    detail_columns[0].caption(
        f"Last automatic search: {last_search['finished_at'] if last_search else 'Not run yet'}"
    )
    detail_columns[1].caption(
        f"Hunter contact credits tracked this month: {hunter_used}/{MONTHLY_HUNTER_LIMIT}"
    )

    st.subheader("Your workflow")
    cards = st.columns(4)
    card_content = [
        (
            "Define your search",
            "Add your profile, preferred roles, locations, seniority, and exclusions.",
            "Set up profile",
            "Profile & companies",
        ),
        (
            "Discover matches",
            "Scan your company watchlist and review explainable fit scores.",
            "Find roles",
            "Matches",
        ),
        (
            "Approve outreach",
            "Edit the strongest drafts and explicitly select every recipient.",
            "Review queue",
            "Approval queue",
        ),
        (
            "Track responses",
            "Follow sent threads and see human or automated replies separately.",
            "View replies",
            "Outreach & replies",
        ),
    ]
    for index, (column, (title, description, label, destination)) in enumerate(zip(cards, card_content), 1):
        column.markdown(
            f'<div class="step-card"><div class="step-number">STEP {index:02}</div>'
            f'<h3>{title}</h3><p>{description}</p></div>',
            unsafe_allow_html=True,
        )
        column.button(
            label,
            key=f"dashboard-{destination}",
            use_container_width=True,
            on_click=navigate_to,
            args=(destination,),
        )

    st.subheader("Recent matches")
    recent = db.ranked_matches()[:5]
    if recent:
        st.dataframe(
            [
                {
                    "Score": item["score"], "Role": item["title"], "Company": item["company_name"],
                    "Location": item["location"] or "Not specified",
                    "Contact": item["contact_email"] or "No published contact",
                    "Status": item["outreach_status"] or item["decision"],
                }
                for item in recent
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        empty_state(
            "Your shortlist will appear here",
            "Complete your profile, then run discovery to rank the best opportunities.",
            "⌁",
        )


def render_generator() -> None:
    model_label = "GroqCloud" if os.getenv("GROQ_API_KEY") else "local Ollama"
    page_header(
        "Draft studio",
        "Write outreach with evidence, not guesswork.",
        f"Generate and review a grounded cold email using {model_label}.",
    )

    candidate_name = st.text_input("Candidate name")
    company_name = st.text_input("Company name")

    st.subheader("Candidate profile")
    candidate_method = st.radio(
        "Candidate profile input method", ["Paste text", "Upload PDF"], horizontal=True,
        label_visibility="collapsed",
    )
    candidate_text = ""
    candidate_pdf = None
    if candidate_method == "Paste text":
        candidate_text = st.text_area(
            "Candidate profile text", height=180,
            placeholder="Paste skills, projects, education, and relevant experience.",
            label_visibility="collapsed",
        )
    else:
        candidate_pdf = st.file_uploader(
            "Candidate profile PDF", type=["pdf"],
            help="Upload a text-based, unencrypted PDF up to 10 MB.",
        )

    st.subheader("Job description")
    job_method = st.radio(
        "Job description input method", ["Paste text", "Upload PDF"], horizontal=True,
        label_visibility="collapsed",
    )
    job_text = ""
    job_pdf = None
    if job_method == "Paste text":
        job_text = st.text_area(
            "Job description text", height=220, placeholder="Paste the complete job description.",
            label_visibility="collapsed",
        )
    else:
        job_pdf = st.file_uploader(
            "Job description PDF", type=["pdf"],
            help="Upload a text-based, unencrypted PDF up to 10 MB.",
        )

    if st.button("Generate email", type="primary", use_container_width=True):
        try:
            candidate_profile = resolve_document_text(
                candidate_method, candidate_text, candidate_pdf.getvalue() if candidate_pdf else None,
                "Candidate profile",
            )
            job_description = resolve_document_text(
                job_method, job_text, job_pdf.getvalue() if job_pdf else None, "Job description",
            )
            request = ColdEmailInput(
                candidate_name=candidate_name, company_name=company_name,
                candidate_profile=candidate_profile, job_description=job_description,
            )
            with st.spinner("Analyzing the role and reviewing your draft..."):
                final_email = generate_cold_email(request, graph=get_graph())
            st.text_area("Final email", value=final_email, height=320)
        except ValidationError as error:
            st.error("\n".join(
                f"{item['loc'][0].replace('_', ' ').title()}: {item['msg']}" for item in error.errors()
            ))
        except DocumentInputError as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"The configured model could not complete the request. Details: {error}")


def render_profile_and_sources(db: Database) -> None:
    page_header(
        "Search identity",
        "Teach the agent what a good role looks like.",
        "Your profile powers matching. Preferences remove obvious misses before any model call.",
    )
    profile = db.get_profile() or {"candidate_name": "", "candidate_profile": "", "preferences": {}}
    preferences = profile["preferences"]
    with st.form("profile"):
        name = st.text_input("Candidate name", profile["candidate_name"])
        text = st.text_area("Candidate profile", profile["candidate_profile"], height=220)
        left, right = st.columns(2)
        titles = left.text_input("Desired titles (comma-separated)", ", ".join(preferences.get("desired_titles", [])))
        locations = right.text_input("Locations (comma-separated)", ", ".join(preferences.get("locations", [])))
        employment = left.text_input(
            "Employment types", ", ".join(preferences.get("employment_types", ["full-time", "intern"])),
        )
        seniority = right.text_input("Seniority terms", ", ".join(preferences.get("seniority", [])))
        excluded = left.text_input("Excluded keywords", ", ".join(preferences.get("excluded_keywords", [])))
        required = right.text_input("Required role keywords", ", ".join(preferences.get("required_keywords", [])))
        remote = right.selectbox(
            "Remote policy", ["any", "remote only"],
            index=1 if preferences.get("remote_policy") == "remote only" else 0,
        )
        score = st.slider("Minimum fit score", 0, 100, int(preferences.get("minimum_score", 70)))
        if st.form_submit_button("Save profile", type="primary"):
            if not name.strip() or not text.strip():
                st.error("Candidate name and profile are required.")
            else:
                desired_titles = comma_values(titles)
                db.save_profile(
                    name, text,
                    {
                        "desired_titles": desired_titles, "locations": comma_values(locations),
                        "employment_types": comma_values(employment), "seniority": comma_values(seniority),
                        "excluded_keywords": comma_values(excluded), "required_keywords": comma_values(required),
                        "remote_policy": remote,
                        "minimum_score": score,
                    },
                )
                if desired_titles:
                    st.success("Profile saved and ready for automatic search.")
                else:
                    st.warning("Profile saved. Add at least one desired title to enable Jooble search.")

    st.divider()
    st.subheader("Company watchlist")
    st.caption("Optional: add direct Greenhouse or Lever boards for higher-quality job descriptions.")
    with st.form("source"):
        company = st.text_input("Company name")
        board_url = st.text_input("Greenhouse or Lever board URL", placeholder="https://boards.greenhouse.io/company")
        if st.form_submit_button("Add company"):
            try:
                provider, token = infer_source(board_url)
                if not company.strip():
                    raise ValueError("Company name is required.")
                db.add_source(company, provider, token, board_url.strip())
                st.success(f"Added {company} ({provider}).")
            except ValueError as error:
                st.error(str(error))
    sources = db.list_sources()
    if sources:
        with st.form("watchlist-status"):
            enabled_values = {
                row["id"]: st.checkbox(
                    f"{row['company_name']} · {row['provider']} · {row['board_url']}",
                    value=bool(row["enabled"]), key=f"source-enabled-{row['id']}",
                )
                for row in sources
            }
            if st.form_submit_button("Save enabled boards"):
                for source_id, enabled in enabled_values.items():
                    db.set_source_enabled(source_id, enabled)
                st.success("Watchlist updated.")


def render_matches(db: Database) -> None:
    page_header(
        "Opportunity radar",
        "A shortlist you can actually act on.",
        "Jooble expands discovery across India and remote roles; direct ATS data is preferred when available. "
        "Apply yourself, then mark the role applied to unlock recruiter outreach.",
    )
    watched_sources = db.list_sources()
    enabled_sources = [source for source in watched_sources if source["enabled"]]
    if enabled_sources:
        st.caption(f"SCANNING {len(enabled_sources)} ENABLED COMPANY BOARDS")
        st.markdown(
            '<div class="connection-strip">' + "".join(
                f'<span class="connection-pill"><span class="connection-dot"></span>'
                f'{html.escape(source["company_name"])} · {html.escape(source["provider"].title())}</span>'
                for source in enabled_sources
            ) + "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.warning("No company boards are enabled. Add one under Search profile or use Jooble discovery.")
    if st.button("Run discovery now", type="primary"):
        with st.spinner("Fetching roles, evaluating fit, and preparing eligible drafts..."):
            try:
                result = run_discovery(db)
                if result.get("jobs", 0):
                    st.success(
                        f"Discovery added or refreshed {result['jobs']} jobs and evaluated "
                        f"{result.get('evaluated', 0)} roles."
                    )
                else:
                    st.warning(
                        "No jobs were retrieved. "
                        f"{result.get('failed_sources', 0)} provider request(s) failed. "
                        "Check network access and API keys, then try again."
                    )
            except Exception as error:
                st.error(str(error))
    all_matches = db.ranked_matches()
    view = st.selectbox(
        "Show",
        ["Top 10 qualified", "New", "Applied", "Contact found", "Outreach queued", "All evaluated"],
    )
    if view == "Top 10 qualified":
        matches = [item for item in all_matches if item["decision"] == "qualified"][:10]
    elif view == "New":
        matches = [item for item in all_matches if item["application_status"] == "discovered"][:10]
    elif view == "Applied":
        matches = [item for item in all_matches if item["application_status"] != "discovered"][:10]
    elif view == "Contact found":
        matches = [item for item in all_matches if item["selected_contact_email"]][:10]
    elif view == "Outreach queued":
        matches = [item for item in all_matches if item["outreach_status"] == "queued"][:10]
    else:
        matches = all_matches[:50]
    if not matches:
        empty_state(
            "No matching roles in this view",
            "Run discovery or switch the filter to see every evaluated opportunity.",
            "⌕",
        )
        return
    for match in matches:
        icon = "✅" if match["decision"] == "qualified" else "—"
        with st.expander(f"{icon} {match['score']} · {match['title']} · {match['company_name']}"):
            st.progress(int(match["score"]), text=f"Fit score · {match['score']}/100")
            st.caption(
                f"{match['location'] or 'Location not specified'}  ·  "
                f"{match['origin_provider'].title()} source  ·  "
                f"{match['description_quality'].title()} description  ·  "
                f"{match['application_status'].replace('_', ' ').title()}"
            )
            if match["posted_at"]:
                st.write(f"Updated: {match['posted_at']}")
            if match["eligibility_warning"]:
                st.warning(match["eligibility_warning"])
            evidence, missing = decoded_list(match["evidence_json"]), decoded_list(match["missing_json"])
            details = st.columns(2)
            with details[0]:
                st.markdown("**Why it fits**")
                if evidence:
                    for item in evidence:
                        st.markdown(f"- {item}")
                else:
                    st.caption("No supporting evidence recorded.")
            with details[1]:
                st.markdown("**Possible gaps**")
                if missing:
                    for item in missing:
                        st.markdown(f"- {item}")
                else:
                    st.caption("No explicit requirements are missing.")
            if match["rejection_reason"]:
                st.write(f"Reason: {match['rejection_reason']}")
            if match["selected_contact_email"]:
                contact_label = " · ".join(
                    value for value in (
                        match["contact_name"], match["contact_position"], match["selected_contact_email"]
                    ) if value
                )
                st.success(
                    f"Recruiting contact: {contact_label} "
                    f"({match['contact_source_kind']}, confidence {match['contact_confidence'] or 'n/a'})"
                )
            elif match["application_status"] == "applied":
                st.info("No verified company recruiting contact has been found yet.")
            action_columns = st.columns(2)
            action_columns[0].link_button(
                "Apply on listing", match["apply_url"] or match["job_url"], use_container_width=True
            )
            if match["application_status"] == "discovered":
                if action_columns[1].button(
                    "Mark as applied", key=f"applied-{match['id']}", use_container_width=True
                ):
                    try:
                        db.mark_applied(match["id"])
                        enrichment = enrich_applied_jobs(db)
                        drafts = prepare_queue(db)
                        st.success(f"Application recorded. Contact search: {enrichment}. Drafts prepared: {drafts}.")
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))
            else:
                action_columns[1].success("Applied")


def render_queue(db: Database) -> None:
    remaining = max(0, DAILY_SEND_LIMIT - db.sent_today())
    page_header(
        "Human checkpoint",
        "Review every message before it leaves.",
        f"{remaining} of {DAILY_SEND_LIMIT} sends remain today. Nothing sends without your confirmation.",
    )
    queue = db.queued_outreach()
    if not queue:
        empty_state(
            "Your approval queue is clear",
            "Apply to a qualified role first; verified-contact drafts will then appear here.",
            "✓",
        )
        return
    selected: list[int] = []
    with st.form("send_batch"):
        for item in queue:
            st.subheader(f"{item['title']} · {item['company_name']}")
            if st.checkbox(f"Select {item['recipient']}", key=f"select-{item['id']}"):
                selected.append(item["id"])
            subject = st.text_input("Subject", item["subject"], key=f"subject-{item['id']}")
            body = st.text_area("Message", item["body"], height=220, key=f"body-{item['id']}")
            db_values = st.session_state.setdefault("draft-values", {})
            db_values[item["id"]] = (subject, body)
            st.caption(f"Recipient source: {item['recipient_source_url']}")
            if item["contact_name"] or item["contact_position"]:
                st.caption(
                    f"Contact: {item['contact_name'] or 'Name unavailable'} · "
                    f"{item['contact_position'] or 'Recruiting contact'} · "
                    f"confidence {item['contact_confidence'] or 'n/a'}"
                )
        confirmed = st.checkbox("I reviewed the selected recipients and final messages.")
        submitted = st.form_submit_button("Send approved batch", type="primary", disabled=remaining == 0)
    if submitted:
        if not selected:
            st.error("Select at least one message.")
        elif not confirmed:
            st.error("Confirm that you reviewed the batch.")
        else:
            try:
                for outreach_id in selected:
                    subject, body = st.session_state["draft-values"][outreach_id]
                    db.update_draft(outreach_id, subject, body)
                result = send_approved(selected, db)
                st.success(f"Batch complete: {result}")
                st.rerun()
            except Exception as error:
                st.error(str(error))


def render_replies(db: Database) -> None:
    page_header(
        "Conversation tracker",
        "Know when outreach turns into a response.",
        "Monitor approved messages and distinguish human replies from automated acknowledgements.",
    )
    if st.button("Check replies now"):
        try:
            with st.spinner("Checking tracked Gmail threads..."):
                st.success(f"Reply check complete: {sync_replies(db, interactive=True)}")
        except Exception as error:
            st.error(str(error))
    rows = db.tracked_outreach()
    if rows:
        st.dataframe(
            [
                {
                    "Company": row["company_name"], "Role": row["title"], "Recipient": row["recipient"],
                    "Status": row["status"], "Sent": row["sent_at"], "Reply": row["replied_at"],
                }
                for row in rows
            ], use_container_width=True, hide_index=True,
        )
    else:
        empty_state("No conversations yet", "Sent outreach and company replies will appear here.", "↗")


def render_settings() -> None:
    page_header(
        "System control",
        "Connections, models, and automation.",
        "Verify your providers and configure scheduled discovery and inbox checks.",
    )
    status = st.columns(3)
    status[0].metric("Inference", "GroqCloud" if os.getenv("GROQ_API_KEY") else "Local Ollama")
    status[1].metric("Job search", "Connected" if os.getenv("JOOBLE_API_KEY") else "Setup needed")
    status[2].metric("Contact search", "Connected" if os.getenv("HUNTER_API_KEY") else "Setup needed")
    st.subheader("Environment")
    st.code(
        "$env:GROQ_API_KEY='your-key'\n"
        "$env:GROQ_MODEL='openai/gpt-oss-20b'\n"
        "$env:JOOBLE_API_KEY='your-key'\n"
        "$env:HUNTER_API_KEY='your-key'",
        language="powershell",
    )
    st.write(
        f"Jooble: {'connected' if os.getenv('JOOBLE_API_KEY') else 'not configured'} · "
        f"Hunter: {'connected' if os.getenv('HUNTER_API_KEY') else 'not configured'}"
    )
    st.write("For Gmail, save OAuth desktop credentials as `.secrets/gmail-client-secret.json`.")
    if st.button("Connect/test Gmail"):
        try:
            gmail = GmailProvider(interactive=True)
            st.success(f"Connected as {gmail.profile['emailAddress']}.")
        except Exception as error:
            st.error(str(error))
    st.write("Install the 8 AM discovery and 15-minute reply tasks from PowerShell:")
    st.code("python automation.py install-scheduler", language="powershell")


NAVIGATION_PAGES = [
    "Dashboard", "Profile & companies", "Matches", "Approval queue",
    "Outreach & replies", "Manual email", "Settings",
]

NAVIGATION_LABELS = {
    "Dashboard": "⌂  Overview",
    "Profile & companies": "◉  Search profile",
    "Matches": "⌕  Opportunity radar",
    "Approval queue": "✓  Approval queue",
    "Outreach & replies": "↗  Conversations",
    "Manual email": "✎  Draft studio",
    "Settings": "⚙  Settings",
}

st.sidebar.markdown(
    """<div class="brand"><div class="brand-mark">S</div><div>
    <div class="brand-name">Scoutly</div><div class="brand-note">Job outreach workspace</div>
    </div></div>""",
    unsafe_allow_html=True,
)
page = st.sidebar.selectbox(
    "Navigation",
    NAVIGATION_PAGES,
    key="navigation",
    format_func=lambda value: NAVIGATION_LABELS[value],
    label_visibility="collapsed",
)
connections = [
    name for name, ready in (
        ("Groq", os.getenv("GROQ_API_KEY")),
        ("Jooble", os.getenv("JOOBLE_API_KEY")),
        ("Hunter", os.getenv("HUNTER_API_KEY")),
    ) if ready
]
st.sidebar.markdown("---")
st.sidebar.caption("CONNECTED SERVICES")
st.sidebar.markdown(
    '<div class="connection-strip">' + "".join(
        f'<span class="connection-pill"><span class="connection-dot"></span>{name}</span>'
        for name in connections
    ) + "</div>",
    unsafe_allow_html=True,
)
st.sidebar.caption("Private local data · reviewed sends")
database = get_database()
if page == "Dashboard":
    render_dashboard(database)
elif page == "Manual email":
    render_generator()
elif page == "Profile & companies":
    render_profile_and_sources(database)
elif page == "Matches":
    render_matches(database)
elif page == "Approval queue":
    render_queue(database)
elif page == "Outreach & replies":
    render_replies(database)
else:
    render_settings()
