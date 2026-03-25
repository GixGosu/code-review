#!/usr/bin/env python3
"""Agent-powered PR review pipeline.

The entire implementation. About 300 lines. No frameworks needed.
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from github import Github

from config import load_config

# --- Prompts (inline — no separate file) ---

SYSTEM_PROMPT = """You are a senior code reviewer. Review the provided diff for:
- Bugs or logic errors
- Security vulnerabilities
- Performance issues
- Code clarity and maintainability

Be {review_style}. Only comment on genuine issues, not style preferences.

Respond with a JSON object:
{{
  "comments": [
    {{
      "line": <line number in the NEW file version>,
      "severity": "critical" | "warning" | "suggestion" | "nitpick",
      "comment": "<your review comment>"
    }}
  ],
  "summary": "<one sentence overall assessment>",
  "approve": <true if no critical/warning issues, false otherwise>
}}

IMPORTANT: "line" must be a line number from the diff with a + prefix (added/modified lines only).
Return only valid JSON, no markdown code blocks."""

USER_PROMPT = """## PR: {title}

{description}

## File: {filename}

```diff
{patch}
```

Review this diff and respond with JSON only."""

SEVERITY_PREFIX = {
    "critical": "[Critical]", "warning": "[Warning]",
    "suggestion": "[Suggestion]", "nitpick": "[Nitpick]",
}
SEVERITY_LEVELS = ["critical", "warning", "suggestion", "nitpick"]


# --- Diff position parsing ---

def build_line_to_position_map(patch: str) -> dict[int, int]:
    """Map new-file line numbers to GitHub diff positions.

    GitHub's review comment API requires a 'position' — the 1-indexed
    line number within the diff/patch text, NOT the file line number.
    This parses @@ -old,count +new,count @@ hunk headers to build
    a mapping from actual line numbers to diff positions.
    """
    if not patch:
        return {}

    line_map = {}
    position = 0
    new_line = None

    for line in patch.split("\n"):
        position += 1

        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk_match:
            new_line = int(hunk_match.group(1))
            continue

        if new_line is None:
            continue

        if line.startswith("-"):
            pass  # Deleted line — no new line number
        elif line.startswith("+"):
            line_map[new_line] = position
            new_line += 1
        else:
            line_map[new_line] = position
            new_line += 1

    return line_map


# --- LLM adapters ---

def review_with_claude(prompt, system, config):
    """Call Claude API."""
    import anthropic
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    resp = client.messages.create(
        model=config.model, max_tokens=2000, temperature=0,
        system=system, messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def review_with_openai(prompt, system, config):
    """Call OpenAI API."""
    from openai import OpenAI
    client = OpenAI(api_key=config.openai_api_key)
    resp = client.chat.completions.create(
        model=config.model, max_tokens=2000, temperature=0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content


def review_file(prompt, system, config):
    """Route to the configured LLM provider."""
    if config.llm_provider == "openai":
        return review_with_openai(prompt, system, config)
    return review_with_claude(prompt, system, config)


# --- Response parsing ---

def parse_response(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences and malformed output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {"comments": [], "summary": "Failed to parse review", "approve": True}


# --- Per-file review (unit of parallelism) ---

def review_single_file(f, pr_title, pr_body, system, config, threshold_idx):
    """Review one file and return (filename, comments, approve, summary)."""
    prompt = USER_PROMPT.format(
        title=pr_title, description=pr_body or "(no description)",
        filename=f.filename, patch=f.patch,
    )

    try:
        response_text = review_file(prompt, system, config)
    except Exception as e:
        return (f.filename, [], True, f"LLM error: {e}")

    result = parse_response(response_text)
    line_map = build_line_to_position_map(f.patch)

    comments = []
    for c in result.get("comments", []):
        severity = c.get("severity", "suggestion")
        if severity not in SEVERITY_LEVELS:
            continue
        if SEVERITY_LEVELS.index(severity) > threshold_idx:
            continue
        line = c.get("line")
        if line not in line_map:
            continue

        comments.append({
            "path": f.filename,
            "position": line_map[line],
            "body": f"{SEVERITY_PREFIX.get(severity, '')} {c.get('comment', '')}",
            "severity": severity,
        })

    return (f.filename, comments, result.get("approve", True), result.get("summary", "N/A"))


# --- Main pipeline ---

def review_pr(config) -> bool:
    """Run the full review pipeline. Returns True if PR should be approved."""
    print(f"Reviewing PR #{config.pr_number} in {config.repo}")

    gh = Github(config.github_token)
    repo = gh.get_repo(config.repo)
    pr = repo.get_pull(config.pr_number)
    print(f"PR: {pr.title} ({pr.changed_files} files changed)")

    # Fetch and filter files
    files = [f for f in pr.get_files() if not config.should_ignore(f.filename) and f.patch]
    print(f"Files to review: {len(files)}")

    if not files:
        print("Nothing to review")
        return True

    # Review files in parallel (one LLM call per file, capped at 10 threads)
    all_comments = []
    should_approve = True
    system = SYSTEM_PROMPT.format(review_style=config.review_style)
    threshold_idx = SEVERITY_LEVELS.index(config.severity_threshold)
    workers = min(len(files), 10)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(review_single_file, f, pr.title, pr.body, system, config, threshold_idx): f
            for f in files
        }
        for future in as_completed(futures):
            filename, comments, approved, summary = future.result()
            print(f"  {filename}: {summary}")
            if not approved:
                should_approve = False
            all_comments.extend(comments)

    # Determine review verdict
    has_critical = any(c["severity"] == "critical" for c in all_comments)
    has_warning = any(c["severity"] == "warning" for c in all_comments)

    if has_critical:
        event = "REQUEST_CHANGES"
        should_approve = False
    elif config.auto_approve and should_approve and not has_warning:
        event = "APPROVE"
    else:
        event = "COMMENT"

    # Post review
    print(f"\nPosting {len(all_comments)} comments ({event})...")

    body = f"Automated review · {len(files)} files reviewed"
    if has_critical:
        body += "\n\n**Critical issues found — requesting changes.**"
    elif not all_comments:
        body += " · No issues found."

    review_comments = [{"path": c["path"], "position": c["position"], "body": c["body"]}
                       for c in all_comments]

    try:
        pr.create_review(body=body, event=event, comments=review_comments)
        print("Done.")
    except Exception as e:
        print(f"Failed to submit review: {e}")
        return False

    return should_approve


def main():
    repo = os.environ.get("REPO") or (sys.argv[1] if len(sys.argv) > 1 else None)
    pr_num = os.environ.get("PR_NUMBER") or (sys.argv[2] if len(sys.argv) > 2 else None)

    if repo:
        os.environ["REPO"] = repo
    if pr_num:
        os.environ["PR_NUMBER"] = str(pr_num)

    try:
        config = load_config()
    except ValueError as e:
        print(f"Config error: {e}")
        sys.exit(1)

    sys.exit(0 if review_pr(config) else 1)


if __name__ == "__main__":
    main()
