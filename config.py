"""Configuration loader for agent-review-pipeline.

Loads from environment variables with optional .reviewrc.yaml overrides.
"""

import os
import fnmatch
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class Config:
    # Required
    github_token: str
    repo: str
    pr_number: int

    # LLM (one of these required)
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

    # Optional with defaults
    model: str = "claude-sonnet-4-20250514"
    review_style: str = "concise"
    auto_approve: bool = False
    severity_threshold: str = "warning"
    ignore_patterns: list[str] = field(default_factory=lambda: [
        "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "*.generated.*", "*.min.js", "*.min.css",
        "dist/**", "build/**", "node_modules/**",
        "*.png", "*.jpg", "*.gif", "*.svg", "*.ico",
        "*.woff", "*.woff2", "*.ttf", "*.eot"
    ])

    def should_ignore(self, filepath: str) -> bool:
        """Check if a file should be ignored based on patterns."""
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(filepath, pattern):
                return True
            # Handle ** patterns
            if "**" in pattern:
                parts = pattern.split("**")
                if len(parts) == 2 and filepath.startswith(parts[0].rstrip("/")):
                    return True
        return False

    @property
    def llm_provider(self) -> str:
        """Determine which LLM provider to use based on model name."""
        if self.model.startswith("gpt"):
            return "openai"
        return "anthropic"


def load_config() -> Config:
    """Load configuration from environment and optional .reviewrc.yaml."""

    # Load from environment
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable required")

    repo = os.environ.get("REPO")
    pr_number_str = os.environ.get("PR_NUMBER")

    if not repo or not pr_number_str:
        raise ValueError("REPO and PR_NUMBER environment variables required")

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        raise ValueError(f"PR_NUMBER must be an integer, got: {pr_number_str}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not anthropic_key and not openai_key:
        raise ValueError("Either ANTHROPIC_API_KEY or OPENAI_API_KEY required")

    config = Config(
        github_token=github_token,
        repo=repo,
        pr_number=pr_number,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
    )

    # Load optional .reviewrc.yaml
    rc_path = Path(".reviewrc.yaml")
    if not rc_path.exists():
        rc_path = Path(".reviewrc.yml")

    if rc_path.exists():
        with open(rc_path) as f:
            rc = yaml.safe_load(f) or {}

        if "model" in rc:
            config.model = rc["model"]
        if "review_style" in rc:
            config.review_style = rc["review_style"]
        if "auto_approve" in rc:
            config.auto_approve = bool(rc["auto_approve"])
        if "severity_threshold" in rc:
            config.severity_threshold = rc["severity_threshold"]
        if "ignore" in rc:
            config.ignore_patterns = rc["ignore"]

    # Validate LLM key for chosen model
    if config.llm_provider == "openai" and not config.openai_api_key:
        raise ValueError(f"Model {config.model} requires OPENAI_API_KEY")
    if config.llm_provider == "anthropic" and not config.anthropic_api_key:
        raise ValueError(f"Model {config.model} requires ANTHROPIC_API_KEY")

    return config
