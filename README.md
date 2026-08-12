# Cold Email Agent

A local cold-email generator built with LangChain, LangGraph, Streamlit, and
Ollama. It compares a candidate profile with a job description, drafts a
personalized email, and runs a bounded grounding-review loop before returning
the final result.

## How the workflow works

LangChain supplies the prompts, Ollama model integration, and structured model
outputs. LangGraph owns the typed workflow state and conditional routing.

```mermaid
flowchart TD
    START([Start]) --> V[Validate input]
    V --> A[Analyze candidate-job fit]
    A --> D[Generate draft]
    D --> R[Review draft]
    R --> C{Approved?}
    C -->|Yes| F[Clean and finalize]
    C -->|No, retries remain| X[Revise from feedback]
    X --> R
    C -->|No, retry limit reached| F
    F --> END([Final email])
```

The analysis and review stages return validated Pydantic objects instead of
free-form control text. In addition to the model review, deterministic checks
enforce the 100–150 word range and ensure the candidate's name appears exactly
once. Revision is capped at two attempts so the graph cannot loop forever.

## Features

- Local inference with Ollama and `llama3.2`
- Typed LangGraph state and conditional review routing
- Structured candidate-job analysis and review decisions
- Grounding rules that separate candidate facts from job requirements
- Bounded automatic revisions
- Streamlit browser interface
- Reusable Python API and backward-compatible CLI
- Mocked tests that do not require Ollama

## Project structure

```text
app.py                 Streamlit interface
main.py                Command-line interface
workflow.py            LangChain prompts and LangGraph workflow
tests/test_workflow.py Mocked workflow and validation tests
requirements.txt       Runtime dependencies
requirements-dev.txt   Test dependencies
```

## Prerequisites

- Python 3.10 or newer
- [Ollama](https://ollama.com/) installed and running
- The Llama 3.2 model downloaded locally

```bash
ollama pull llama3.2
```

## Installation

Create and activate a virtual environment, then install the dependencies:

```bash
python -m venv venv
```

On Windows:

```powershell
venv\Scripts\activate
pip install -r requirements.txt
```

For development and testing:

```powershell
pip install -r requirements-dev.txt
```

## Run the Streamlit app

```powershell
streamlit run app.py
```

Enter the candidate name, company name, candidate profile, and complete job
description. The UI shows only the finalized email; analysis and review details
remain internal to the workflow.

## Run the CLI

The original interactive workflow remains available:

```powershell
python main.py
```

Type `END` on a new line after the candidate profile and job description.

## Python API

```python
from workflow import ColdEmailInput, generate_cold_email

email = generate_cold_email(
    ColdEmailInput(
        candidate_name="Ada Lovelace",
        company_name="Example Company",
        candidate_profile="Python developer who built a scheduling application.",
        job_description="Software Engineer role requiring Python development.",
    )
)
```

Use `build_graph(model=..., max_revisions=...)` to inject another compatible
chat model or change the revision cap. Set `OLLAMA_MODEL` to select a different
locally installed Ollama model without changing the code.

## Tests

```powershell
pytest
```

The tests inject deterministic LangChain runnables and cover direct approval,
revision routing, the retry cap, deterministic validation, and output cleaning.
