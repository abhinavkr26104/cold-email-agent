"""Backward-compatible command-line entry point."""

from workflow import ColdEmailInput, generate_cold_email


def read_multiline(label: str) -> str:
    print(f"\nPaste {label}.")
    print("Type END on a new line when finished:\n")
    lines: list[str] = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            return "\n".join(lines)
        lines.append(line)


def main() -> None:
    print("\n=== COLD EMAIL LLM AGENT ===\n")
    request = ColdEmailInput(
        candidate_name=input("Candidate Name: "),
        company_name=input("Company Name: "),
        candidate_profile=read_multiline("Candidate Profile"),
        job_description=read_multiline("Job Description"),
    )
    print("\n--- FINAL EMAIL ---\n")
    print(generate_cold_email(request))


if __name__ == "__main__":
    main()
