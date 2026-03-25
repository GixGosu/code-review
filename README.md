# Agent Review Pipeline

This is the entire architecture behind most AI code review tools. It's about 300 lines of Python.

```
GitHub PR (webhook or CLI trigger)
    ↓
Fetch diff via GitHub API
    ↓
Chunk by file → filter noise (lockfiles, generated code, assets)
    ↓
For each chunk: build review prompt with context
    ↓
LLM call (Claude or GPT-4 via API)
    ↓
Parse structured response (approve / comment / request-change)
    ↓
Post review comments via GitHub API
```

That's it. That's the whole product.

## Setup

```bash
git clone https://github.com/GixGosu/code-review.git
cd code-review
pip install -r requirements.txt
```

Create a `.env` file:

```bash
GITHUB_TOKEN=ghp_your_token_here
ANTHROPIC_API_KEY=sk-ant-your-key-here  # or use OPENAI_API_KEY
```

## Usage

### CLI

```bash
# Review a specific PR
python review.py owner/repo 123

# Or set environment variables
REPO=owner/repo PR_NUMBER=123 python review.py
```

### GitHub Action

Add to `.github/workflows/review.yml`:

```yaml
name: Agent Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python review.py
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          REPO: ${{ github.repository }}
```

## Configuration

Optional `.reviewrc.yaml`:

```yaml
ignore:
  - "*.lock"
  - "*.generated.*"
  - "dist/**"

model: claude-sonnet-4-20250514  # or gpt-4o
review_style: concise            # or detailed
auto_approve: false              # approve PRs with no critical findings
severity_threshold: warning      # only post comments at this level or above
```

## How It Works

1. **Fetch the diff** — Uses GitHub API to get the PR's file changes. Skips lockfiles, generated code, and binary assets.

2. **Chunk by file** — Each changed file becomes a review unit. The file's diff and language context go to the LLM.

3. **Build the prompt** — System prompt defines the reviewer role. User prompt includes PR title, description, and the diff. Response format is structured JSON.

4. **Call the LLM** — One API call per file, parallelized. Claude Sonnet 4 (`claude-sonnet-4-20250514`) or GPT-4o. Temperature 0 for deterministic output. A typical 10-file PR costs ~$0.02-0.05.

5. **Parse the response** — Extract JSON array of comments with file, line, severity, and comment text. Retry logic handles malformed JSON.

6. **Post comments** — Map line numbers to GitHub diff positions (this is the only tricky part). Post as PR review comments. Final verdict based on severity.

## Fork PR Limitations

When reviewing PRs from forks, the default `GITHUB_TOKEN` has limited permissions. For fork PRs:

- The token cannot write review comments directly
- Use a Personal Access Token (PAT) with `repo` scope instead
- Or configure the action to run with elevated permissions via `pull_request_target` (use with caution)

## What This Intentionally Does NOT Include

- **MCP or any protocol layer** — direct API calls are simpler
- **LangChain / any framework** — unnecessary for a linear pipeline
- **Vector databases or RAG** — the diff IS the context
- **Agent loops** — one pass, structured output, done
- **A web dashboard** — it's a CLI tool and a GitHub Action

## Cost

- Claude Sonnet 4: ~$3/M input, ~$15/M output tokens
- GPT-4o: ~$2.50/M input, ~$10/M output tokens
- Typical PR review: $0.02-0.10

## License

MIT

---

If someone is charging you a monthly fee for this, now you know what you're paying for.

<!-- TODO: Add LinkedIn post link once published -->
