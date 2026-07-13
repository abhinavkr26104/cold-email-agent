from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

def clean_email(email, candidate_name):
    lines = email.strip().splitlines()

    cleaned_lines = []
    previous_line = None

    for line in lines:
        stripped_line = line.strip()

        if stripped_line == previous_line:
            continue

        cleaned_lines.append(line)
        previous_line = stripped_line

    return "\n".join(cleaned_lines)


llm = ChatOllama(
    model="llama3.2",
    temperature=0.3
)



# CHAIN 1: CONTEXT ANALYSIS


context_prompt = ChatPromptTemplate.from_template("""
You are a job outreach analysis agent.

The CANDIDATE is looking for a job and wants to cold email
a recruiter or hiring manager.

Analyze the candidate profile and job description.

Candidate Profile:
{candidate_profile}

Job Description:
{job_description}

Identify:
1. The three most relevant candidate skills.
2. The main requirement of the job.
3. The strongest connection between the candidate and the job.
4. The best angle the CANDIDATE should use when emailing the recruiter.

Do NOT write the email.

Return only a concise analysis.
""")

context_chain = context_prompt | llm



# CHAIN 2: EMAIL GENERATION


email_prompt = ChatPromptTemplate.from_template("""
You are a professional cold email writing agent.

The email is FROM a job candidate TO a recruiter or hiring manager.

Candidate Name:
{candidate_name}

Company Name:
{company_name}

Job Description:
{job_description}

Candidate Profile:
{candidate_profile}

Context Analysis:
{context_analysis}

Write a personalized cold email using ONLY facts explicitly provided
in the Candidate Profile, Job Description, and Context Analysis.

STRICT RULES:
- The candidate is asking about the job opportunity.
- Keep the email between 100 and 150 words.
- Use a professional and confident tone.
- Mention only skills and projects explicitly stated in the candidate profile.
- Use the exact job title from the job description.
- Do not invent years of experience.
- Do not call the candidate "seasoned", "expert", or "experienced" unless stated.
- Do not invent technologies, frameworks, achievements, or business impact.
- Avoid generic phrases such as "drive business growth".
- Include one specific candidate project if relevant.
- End with a polite call to action.
- Do not use placeholders.

Return only the email.
""")

email_chain = email_prompt | llm


# -------------------------
# INPUT DATA
# -------------------------

print("\n=== COLD EMAIL LLM AGENT ===\n")

candidate_name = input("Candidate Name: ")
company_name = input("Company Name: ")

print("\nPaste Candidate Profile.")
print("Type END on a new line when finished:\n")

candidate_lines = []

while True:
    line = input()

    if line.strip().upper() == "END":
        break

    candidate_lines.append(line)

candidate_profile = "\n".join(candidate_lines)


print("\nPaste Job Description.")
print("Type END on a new line when finished:\n")

job_lines = []

while True:
    line = input()

    if line.strip().upper() == "END":
        break

    job_lines.append(line)

job_description = "\n".join(job_lines)


# VALIDATE INPUTS
if not candidate_name.strip():
    raise ValueError("Candidate name cannot be empty.")

if not company_name.strip():
    raise ValueError("Company name cannot be empty.")

if not candidate_profile.strip():
    raise ValueError("Candidate profile cannot be empty.")

if not job_description.strip():
    raise ValueError("Job description cannot be empty.")


# -------------------------
# RUN CHAIN 1
# -------------------------

context_response = context_chain.invoke({
    "candidate_profile": candidate_profile,
    "job_description": job_description
})

context_analysis = context_response.content

print("\n--- CONTEXT ANALYSIS ---\n")
print(context_analysis)


# RUN CHAIN 2


email_response = email_chain.invoke({
    "candidate_name": candidate_name,
    "company_name": company_name,
    "job_description": job_description,
    "candidate_profile": candidate_profile,
    "context_analysis": context_analysis
})

review_prompt = ChatPromptTemplate.from_template("""
You are an email grounding and quality review agent.

Candidate Profile:
{candidate_profile}

Job Description:
{job_description}

Generated Email:
{generated_email}

Review the generated email.

Your task:
1. Identify claims not explicitly supported by the candidate profile.
2. Identify incorrect or invented job titles.
3. Remove invented achievements, experience, or attachments.
4. Rewrite generic corporate language to sound natural.
5. Preserve only grounded candidate information.
6. Keep the final email professional and concise.

Return ONLY the corrected final email.
""")

review_chain = review_prompt | llm



generated_email = email_response.content

print("\n--- DRAFT EMAIL ---\n")
print(generated_email)


# -------------------------
# RUN CHAIN 3
# -------------------------

review_response = review_chain.invoke({
    "candidate_profile": candidate_profile,
    "job_description": job_description,
    "generated_email": generated_email
})

print("\n--- REVIEWED FINAL EMAIL ---\n")
final_email = clean_email(
    review_response.content,
    candidate_name
)

print(final_email)