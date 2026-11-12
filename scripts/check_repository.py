"""Fail CI on tracked local env files or likely live credentials."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".sh", ".txt", ".css", ".js", ".html"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b[A-Za-z_]*api[_-]?key\s*[:=]\s*['\"][^'\"]{12,}['\"]", re.I),
)
MODEL_PATTERN = re.compile(
    r"(?<![\w/])(?:gemini|groq|openai|anthropic|ollama|mistral)/[A-Za-z0-9][A-Za-z0-9_.:-]+",
    re.I,
)


def scan_text(name: str, content: str) -> list[str]:
    problems = []
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        problems.append(f"likely credential in: {name}")
    path = Path(name)
    if path.suffix in {".py", ".yaml", ".yml"} or path.name == "Dockerfile":
        identifiers = set(MODEL_PATTERN.findall(content)) - {"ollama/ollama:0.5.13"}
        if identifiers:
            problems.append(f"model identifier in: {name}")
    return problems


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    problems = []
    for name in filter(None, tracked):
        path = Path(name)
        if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            problems.append(f"tracked local environment file: {name}")
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {".env.example", "Dockerfile"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        problems.extend(scan_text(name, content))
    for problem in problems:
        print(problem)
    if not problems:
        print("Tracked configuration and credential patterns: clean")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
