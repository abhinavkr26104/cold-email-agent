"""Streamlit interface for the cold-email workflow."""

import streamlit as st
from pydantic import ValidationError

from workflow import ColdEmailInput, build_graph, generate_cold_email


st.set_page_config(page_title="Cold Email Agent", page_icon="✉️", layout="centered")


@st.cache_resource
def get_graph():
    return build_graph()


st.title("Cold Email Agent")
st.caption("Generate a grounded, personalized cold email with a local Ollama model.")

with st.form("cold_email_form"):
    candidate_name = st.text_input("Candidate name")
    company_name = st.text_input("Company name")
    candidate_profile = st.text_area(
        "Candidate profile",
        height=180,
        placeholder="Paste skills, projects, education, and relevant experience.",
    )
    job_description = st.text_area(
        "Job description",
        height=220,
        placeholder="Paste the complete job description.",
    )
    submitted = st.form_submit_button("Generate email", type="primary", use_container_width=True)

if submitted:
    try:
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
    except Exception as error:
        st.error(
            "The local model could not complete the request. Confirm that Ollama is "
            "running and that `ollama pull llama3.2` has completed. "
            f"Details: {error}"
        )
