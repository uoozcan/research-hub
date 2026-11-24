"""Tool-free, bounded Codex synthesis over a deliberately small public input."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class SynthError(Exception):
    pass


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Summary(Entry):
    id: str
    finding: str
    relevance: str
    limitation: str


class Idea(Entry):
    title: str
    kind: Literal["current_project", "new_project"]
    project: str
    hypothesis: str
    record_ids: list[str]
    first_test: str
    success_criterion: str
    uncertainty: str


class Synthesis(Entry):
    summaries: list[Summary]
    ideas: list[Idea]


def validate(output, payload):
    try:
        parsed = Synthesis.model_validate_json(output)
    except ValidationError as exc:
        raise SynthError("Codex returned an invalid briefing") from exc
    ids = {r["id"] for r in payload["records"]}
    summary_ids = set()
    for summary in parsed.summaries:
        if (summary.id not in ids or summary.id in summary_ids
                or any(not 1 <= len(field.strip()) <= 600
                       for field in (summary.finding, summary.relevance, summary.limitation))):
            raise SynthError("Codex returned an invalid evidence reference or summary")
        summary_ids.add(summary.id)
    if len(parsed.ideas) > 3 or (ids and not summary_ids):
        raise SynthError("Codex returned an invalid number of items")
    for idea in parsed.ideas:
        if (not 1 <= len(idea.record_ids) <= 5 or set(idea.record_ids) - ids
                or (idea.kind == "current_project" and idea.project not in payload["project_briefs"])
                or (idea.kind == "new_project" and idea.project != "")
                or any(not 1 <= len(field.strip()) <= 600 for field in (
                    idea.title, idea.hypothesis, idea.first_test, idea.success_criterion, idea.uncertainty))):
            raise SynthError("Codex returned an invalid idea or citation")
    data = parsed.model_dump()
    data["summaries"] = {s.pop("id"): s for s in data["summaries"]}
    return data


def _desktop_codex(executable):
    if executable != "codex":
        return executable
    on_path = shutil.which(executable)
    if on_path:
        return on_path
    local = os.environ.get("LOCALAPPDATA")
    if local:
        versions = Path(local) / "OpenAI" / "Codex" / "bin"
        candidates = list(versions.glob("*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))
    return executable


def synthesize(root, executable, payload):
    if not executable:
        raise SynthError("Codex synthesis is disabled; showing the rules-based digest")
    try:
        prompt = (root / "prompts" / "MORNING_SYNTHESIS.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="research-hub-codex-") as temporary:
            workdir = Path(temporary)
            schema = workdir / "schema.json"
            output = workdir / "result.json"
            schema.write_text(json.dumps(Synthesis.model_json_schema()), encoding="utf-8")
            command = [
                _desktop_codex(executable), "--ask-for-approval", "never", "exec", "--ephemeral", "--ignore-user-config",
                "--sandbox", "read-only", "--disable", "shell_tool", "-c", 'web_search="disabled"',
                "-c", "mcp_servers={}", "--skip-git-repo-check", "--cd", temporary, "--output-schema",
                str(schema), "--output-last-message", str(output), "--json", "-",
            ]
            proc = subprocess.run(command, cwd=workdir, input=prompt + "\n\n" + json.dumps(payload, ensure_ascii=False),
                                  encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=240, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.returncode or not output.is_file():
                raise SynthError("Codex CLI did not complete; check its installation and sign-in")
            for line in proc.stdout.splitlines():
                if not line.startswith("{"):
                    continue
                item = json.loads(line).get("item", {})
                if item.get("type") in {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"}:
                    raise SynthError("Codex attempted a tool call during briefing synthesis")
            return validate(output.read_bytes(), payload)
    except subprocess.TimeoutExpired as exc:
        raise SynthError("Codex briefing timed out") from exc
    except (OSError, ValueError) as exc:
        raise SynthError("Codex CLI or its briefing files are unavailable") from exc
