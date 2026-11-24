#!/usr/bin/env python3
"""Stream Codex output to the Actions log without exposing the private brief."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SECRET_KEYS = ("AI_GATEWAY_API_KEY", "GITHUB_TOKEN")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def load_private(task_path: str) -> str:
    task = Path(task_path)
    if not task.is_file():
        return ""
    text = task.read_text(encoding="utf-8", errors="replace")
    start = "<!-- agent-brief:start -->"
    end = "<!-- agent-brief:end -->"
    if start not in text or end not in text:
        return ""
    return text.split(start, 1)[1].split(end, 1)[0]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: stream_agent_log.py TASK_FILE [TASK_FILE...]", file=sys.stderr)
        return 2

    private = "\n".join(filter(None, (load_private(path) for path in sys.argv[1:])))
    private_lines = {
        normalize(line)
        for line in private.splitlines()
        if len(line.strip()) >= 12
    }
    private_ngrams: set[str] = set()
    for line in private_lines:
        words = re.findall(r"[a-z0-9]+", line)
        for index in range(len(words) - 2):
            private_ngrams.add(" ".join(words[index:index + 3]))

    secrets = [
        os.environ[key]
        for key in SECRET_KEYS
        if len(os.environ.get(key, "")) >= 8
    ]

    def sanitize(value: str) -> str:
        value = ANSI.sub("", value).replace("\r", "")
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[REDACTED]", value)
        value = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9]{20,}\b", "[REDACTED]", value)
        value = re.sub(
            r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            value,
        )
        return value[:1000]

    def is_private(value: str) -> bool:
        normalized = normalize(value)
        if any(line in normalized for line in private_lines):
            return True
        return any(ngram in normalized for ngram in private_ngrams)

    for raw in sys.stdin:
        line = sanitize(raw).rstrip("\n")
        if not line.strip() or is_private(line):
            continue
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
