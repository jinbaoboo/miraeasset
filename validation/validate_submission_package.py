"""Audit the Git repository against the contest submission package contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
MAX_TRACKED_BYTES = 50 * 1024 * 1024
REQUIRED_PATHS = (
    "README.md", "SUBMISSION.md", "DATA_GUIDE.md", "Dockerfile", "Makefile",
    "requirements.txt", "pyproject.toml", ".env.example", ".gitignore", ".dockerignore",
    "src", "tests", "eval", "validation", "schemas", "docs",
    "docs/technical_proposal.md", "docs/api_spec.md", "docs/implementation_report.md",
    "docs/release_checklist.md", "docs/roadmap_qa_400_validation.md",
    "schemas/answer_response.schema.json",
    "eval/build_lifecycle_qa_100.py", "eval/lifecycle_qa_100_questions.jsonl",
    "eval/lifecycle_qa_100_results.json", "eval/build_composite_calculation_qa_100.py",
    "eval/composite_calculation_qa_100_questions.jsonl", "eval/composite_calculation_qa_100_results.json",
    "eval/build_unanswerable_security_qa_100.py", "eval/unanswerable_security_qa_100_questions.jsonl",
    "eval/unanswerable_security_qa_100_results.json", "eval/build_noisy_language_qa_100.py",
    "eval/noisy_language_qa_100_questions.jsonl", "eval/noisy_language_qa_100_results.json",
)
FORBIDDEN_TRACKED_NAMES = {".env"}
FORBIDDEN_TRACKED_PREFIXES = ("outputs/", "corpus/", "tmp/", ".venv/")
FORBIDDEN_TRACKED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pem", ".key", ".log")
SECRET_ENV_KEYS = ("HCX_API_KEY", "HCX_APIGW_KEY")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _tracked_files() -> List[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    ).stdout
    return [value for value in output.decode("utf-8").split("\0") if value]


def _env_values(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def validate() -> Dict[str, Any]:
    tracked = set(_tracked_files())
    missing_paths = [path for path in REQUIRED_PATHS if not (ROOT / path).exists()]
    untracked_required_files = [
        path for path in REQUIRED_PATHS
        if (ROOT / path).is_file() and path not in tracked
    ]
    forbidden_tracked = sorted(
        path for path in tracked
        if path in FORBIDDEN_TRACKED_NAMES
        or path.startswith(FORBIDDEN_TRACKED_PREFIXES)
        or path.lower().endswith(FORBIDDEN_TRACKED_SUFFIXES)
    )
    oversized_tracked = sorted([
        {"path": path, "bytes": (ROOT / path).stat().st_size}
        for path in tracked
        if (ROOT / path).is_file() and (ROOT / path).stat().st_size > MAX_TRACKED_BYTES
    ], key=lambda item: item["path"])
    private_key_files = []
    for path in tracked:
        file_path = ROOT / path
        if not file_path.is_file() or file_path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        private_key_marker = "PRIVATE " + "KEY-----"
        if private_key_marker in text:
            private_key_files.append(path)
    env_values = _env_values(ROOT / ".env.example") if (ROOT / ".env.example").is_file() else {}
    nonempty_secret_examples = {
        key: bool(env_values.get(key)) for key in SECRET_ENV_KEYS if env_values.get(key)
    }
    checks = {
        "required_paths_present": not missing_paths,
        "required_files_tracked": not untracked_required_files,
        "forbidden_artifacts_not_tracked": not forbidden_tracked,
        "tracked_files_under_50mb": not oversized_tracked,
        "private_keys_absent": not private_key_files,
        "secret_examples_empty": not nonempty_secret_examples,
        "origin_configured": bool(_git("remote", "get-url", "origin")),
        "branch_is_main": _git("branch", "--show-current") == "main",
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "details": {
            "missing_paths": missing_paths,
            "untracked_required_files": untracked_required_files,
            "forbidden_tracked": forbidden_tracked,
            "oversized_tracked": oversized_tracked,
            "private_key_files": sorted(private_key_files),
            "nonempty_secret_examples": nonempty_secret_examples,
            "origin": _git("remote", "get-url", "origin"),
            "branch": _git("branch", "--show-current"),
            "tracked_file_count": len(tracked),
        },
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
