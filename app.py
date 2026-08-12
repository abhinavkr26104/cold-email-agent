"""Streamlit interface for the cold-email workflow."""

import streamlit as st
from pydantic import ValidationError

from document_input import DocumentInputError, resolve_document_text
from workflow import ColdEmailInput, build_graph, generate_cold_email


st.set_page_config(page_title="Cold Email Agent", page_icon="✉️", layout="centered")


@st.cache_resource
def get_graph():
    return build_graph()


st.title("Cold Email Agent")
st.caption("Generate a grounded, personalized cold email with a local Ollama model.")

candidate_name = st.text_input("Candidate name")
company_name = st.text_input("Company name")

st.subheader("Candidate profile")
candidate_method = st.radio(
    "Candidate profile input method",
    ["Paste text", "Upload PDF"],
    horizontal=True,
    label_visibility="collapsed",
)
candidate_text = ""
candidate_pdf = None
if candidate_method == "Paste text":
    candidate_text = st.text_area(
        "Candidate profile text",
        height=180,
        placeholder="Paste skills, projects, education, and relevant experience.",
        label_visibility="collapsed",
    )
else:
    candidate_pdf = st.file_uploader(
        "Candidate profile PDF",
        type=["pdf"],
        help="Upload a text-based, unencrypted PDF up to 10 MB.",
    )

st.subheader("Job description")
job_method = st.radio(
    "Job description input method",
    ["Paste text", "Upload PDF"],
    horizontal=True,
    label_visibility="collapsed",
)
job_text = ""
job_pdf = None
if job_method == "Paste text":
    job_text = st.text_area(
        "Job description text",
        height=220,
        placeholder="Paste the complete job description.",
        label_visibility="collapsed",
    )
else:
    job_pdf = st.file_uploader(
        "Job description PDF",
        type=["pdf"],
        help="Upload a text-based, unencrypted PDF up to 10 MB.",
    )

submitted = st.button("Generate email", type="primary", use_container_width=True)

if submitted:
    try:
        candidate_profile = resolve_document_text(
            candidate_method,
            candidate_text,
            candidate_pdf.getvalue() if candidate_pdf else None,
            "Candidate profile",
        )
        job_description = resolve_document_text(
            job_method,
            job_text,
            job_pdf.getvalue() if job_pdf else None,
            "Job description",
        )
        request = ColdEmailInput(
            candidate_name=candidate_name,
            company_name=company_name,
            candidate_profile=candidate_profile,
            job_description=job_description,
        )
        with st.spinner("Analyzing the role and reviewing your draft..."):
            final_email = generate_cold_email(request, graph=get_graph())
        st.text_area("Final email", value=final_email, height=320)
    except ValidationError as error:
        messages = [
            f"{item['loc'][0].replace('_', ' ').title()}: {item['msg']}"
            for item in error.errors()
        ]
        st.error("\n".join(messages))
    except DocumentInputError as error:
        st.error(str(error))
    except Exception as error:
        st.error(
            "The local model could not complete the request. Confirm that Ollama is "
            "running and that `ollama pull llama3.2` has completed. "
            f"Details: {error}"
        )
