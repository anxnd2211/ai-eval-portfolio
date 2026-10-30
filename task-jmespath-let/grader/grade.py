#!/usr/bin/env python3
"""Tamper-resistant grader for the "JMESPath let-expressions" task.

Usage (inside the task image, grader directory mounted read-only):

    python /grader/grade.py --patch /patch.diff [--out report.json]

What it does, in order:

 1. Resets the repository to the pinned base commit (tag ``task-base``) so every
    run starts from identical state, whatever an agent left behind.
 2. Enforces a *path policy* on the candidate patch before touching anything:
    only ``jmespath/``, ``docs/``, ``CHANGELOG.rst`` and ``README.rst`` may change;
    no files under ``tests/``, no ``conftest.py``, no packaging or pytest config,
    no dotfiles, no renames/deletes, no executable bits, no binaries, size caps.
    A rejected patch scores 0 without running a single test.
 3. Applies the patch with ``git apply`` (``--check`` first).
 4. Scans the patch text for signals that deserve a human look (``import pytest``
    inside the library, ``sys.modules`` games, subprocess/network use). These are
    *warnings*, never rejections: they are easy to false-positive on.
 5. Verifies the hidden test files against SHA-256 hashes in ``manifest.json``
    and copies them into ``tests/`` only after the patch is applied.
 6. Runs the hidden tests with a timeout and parses the JUnit XML. Every test
    must pass; the collected count must equal the manifest's count (so a crash
    during collection or a deselection trick cannot look like a pass); skipped
    or xfailed tests count as failures.
 7. Runs the pre-existing suite (regression guard): zero failures/errors and the
    same collected count as the pinned baseline.
 8. Emits one JSON report. ``score`` is binary (1.0 / 0.0);
    ``hidden_pass_fraction`` is also reported for reward shaping.
 9. Resets the repository again so the container can be reused.

Exit codes: 0 = graded (see ``score``), 2 = grader-internal error (bad manifest,
missing git, unreadable inputs). A candidate can never produce exit 2 on purpose.

Only the standard library is used.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# --------------------------------------------------------------------------- policy
ALLOWED_DIR_PREFIXES = ("jmespath/", "docs/")
ALLOWED_FILES = {"CHANGELOG.rst", "README.rst"}
DENIED_BASENAMES = {
    "conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "setup.py",
    "pyproject.toml", "sitecustomize.py", "usercustomize.py", "MANIFEST.in",
}
DENIED_SUFFIXES = (".pth", ".pyc", ".so", ".dylib", ".dll")
MAX_PATCH_BYTES = 300_000
MAX_FILES = 25

# Soft signals: reported as warnings for human review, never auto-rejected.
SUSPICIOUS_PATTERNS = [
    (r"^\+.*\bimport\s+pytest\b", "imports pytest inside the library"),
    (r"^\+.*\bfrom\s+pytest\b", "imports pytest inside the library"),
    (r"^\+.*\bsys\.modules\b", "touches sys.modules"),
    (r"^\+.*\bos\.environ\b", "reads/writes environment variables"),
    (r"^\+.*\bsubprocess\b", "uses subprocess"),
    (r"^\+.*\b(socket|urllib|http\.client|requests)\b", "network-capable import"),
    (r"^\+.*\b__import__\(", "dynamic import"),
    (r"^\+.*\bexec\(|^\+.*\beval\(", "exec/eval"),
    (r"^\+.*\bmonkeypatch\b", "monkeypatch"),
    (r"^\+.*\bunittest\.(mock|skip)\b", "test-framework hooks inside library"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None, timeout=None, env=None):
    return subprocess.run(
        cmd, cwd=cwd, timeout=timeout, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


class GraderError(RuntimeError):
    """Infrastructure problem: not attributable to the candidate patch."""


def git(repo: Path, *args, check=True):
    proc = run(["git", "-C", str(repo), *args])
    if check and proc.returncode != 0:
        raise GraderError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


# --------------------------------------------------------------------------- steps
def reset_repo(repo: Path) -> None:
    ref = "task-base"
    if git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{ref}", check=False).returncode:
        ref = "HEAD"
    git(repo, "reset", "-q", "--hard", ref)
    git(repo, "clean", "-fdxq")
    status = git(repo, "status", "--porcelain").stdout.strip()
    if status:
        raise GraderError(f"repository not clean after reset:\n{status}")


def path_allowed(path: str) -> str | None:
    """Return None if the path may be modified, else a human-readable reason."""
    norm = path.strip()
    if not norm or norm.startswith("/") or ".." in norm.split("/"):
        return "absolute or parent-relative path"
    parts = norm.split("/")
    if any(p.startswith(".") for p in parts):
        return "dotfile / dot-directory"
    base = parts[-1]
    if base in DENIED_BASENAMES:
        return f"{base} is never allowed"
    if base.endswith(DENIED_SUFFIXES):
        return f"file type {Path(base).suffix!r} is never allowed"
    if norm.startswith("tests/"):
        return "tests/ is read-only for candidates"
    if norm in ALLOWED_FILES or norm.startswith(ALLOWED_DIR_PREFIXES):
        return None
    return "outside the allowed paths (jmespath/, docs/, CHANGELOG.rst, README.rst)"


def check_patch_policy(repo: Path, patch: Path, report: dict) -> bool:
    pol = report["policy"]
    size = patch.stat().st_size
    pol["patch_bytes"] = size
    if size == 0:
        pol["note"] = "empty patch (baseline run)"
        return True
    if size > MAX_PATCH_BYTES:
        pol["violations"].append(f"patch too large: {size} bytes > {MAX_PATCH_BYTES}")
        return False

    numstat = run(["git", "-C", str(repo), "apply", "--numstat", "--", str(patch)])
    if numstat.returncode != 0:
        pol["violations"].append(f"patch is not parseable by git: {numstat.stderr.strip()[:500]}")
        return False
    summary = run(["git", "-C", str(repo), "apply", "--summary", "--", str(patch)]).stdout

    files = []
    for line in numstat.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        added, deleted, path = cols[0], cols[1], cols[2]
        if added == "-" or deleted == "-":
            pol["violations"].append(f"binary change not allowed: {path}")
        files.append(path)
    pol["files"] = files
    if len(files) > MAX_FILES:
        pol["violations"].append(f"too many files changed: {len(files)} > {MAX_FILES}")
    for path in files:
        reason = path_allowed(path)
        if reason:
            pol["violations"].append(f"{path}: {reason}")
    for line in summary.splitlines():
        low = line.strip().lower()
        if low.startswith(("rename", "delete", "copy")) or "mode change" in low:
            pol["violations"].append(f"disallowed operation: {line.strip()}")
        if low.startswith("create mode") and not low.startswith("create mode 100644"):
            pol["violations"].append(f"new file must be a plain 0644 file: {line.strip()}")
    return not pol["violations"]


def scan_patch_text(patch: Path, report: dict) -> None:
    text = patch.read_text(errors="replace")
    seen = set()
    for pattern, label in SUSPICIOUS_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.MULTILINE):
            key = (label, m.group(0).strip()[:120])
            if key not in seen:
                seen.add(key)
                report["policy"]["warnings"].append({"signal": label, "line": key[1]})


def apply_patch(repo: Path, patch: Path, report: dict) -> bool:
    if patch.stat().st_size == 0:
        report["apply"] = {"applied": False, "note": "empty patch"}
        return True
    check = run(["git", "-C", str(repo), "apply", "--check", "--whitespace=nowarn", "--", str(patch)])
    if check.returncode != 0:
        report["apply"] = {"applied": False, "error": check.stderr.strip()[:2000]}
        return False
    proc = run(["git", "-C", str(repo), "apply", "--whitespace=nowarn", "--", str(patch)])
    if proc.returncode != 0:
        report["apply"] = {"applied": False, "error": proc.stderr.strip()[:2000]}
        return False
    touched = git(repo, "status", "--porcelain").stdout.splitlines()
    touched_paths = [t[3:].strip() for t in touched]
    # Belt and braces: re-validate what actually changed on disk.
    for p in touched_paths:
        reason = path_allowed(p)
        if reason:
            report["policy"]["violations"].append(f"{p}: {reason} (post-apply)")
    report["apply"] = {"applied": True, "touched": touched_paths}
    return not report["policy"]["violations"]


def install_hidden_tests(repo: Path, tests_dir: Path, manifest: dict, report: dict) -> list[str]:
    installed = []
    for rel, expected in manifest["hidden_tests"].items():
        src = tests_dir / Path(rel).name
        if not src.is_file():
            raise GraderError(f"hidden test file missing: {src}")
        actual = sha256(src)
        if actual != expected:
            raise GraderError(f"hidden test hash mismatch for {rel}: {actual} != {expected}")
        dst = repo / rel
        if dst.exists():
            raise GraderError(f"{rel} already exists in the repo after reset; refusing to overwrite")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        installed.append(rel)
    report["hidden_tests_installed"] = installed
    return installed


def parse_junit(xml_path: Path) -> dict:
    out = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0,
           "failed_ids": [], "skipped_ids": [], "error_ids": []}
    if not xml_path.is_file():
        out["errors"] = 1
        out["error_ids"].append("<no junit report produced>")
        return out
    root = ET.parse(xml_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for case in suite.iter("testcase"):
            out["tests"] += 1
            tid = f"{case.get('classname', '')}::{case.get('name', '')}"
            if case.find("failure") is not None:
                out["failures"] += 1
                out["failed_ids"].append(tid)
            elif case.find("error") is not None:
                out["errors"] += 1
                out["error_ids"].append(tid)
            elif case.find("skipped") is not None:
                out["skipped"] += 1
                out["skipped_ids"].append(tid)
    return out


def run_pytest(repo: Path, args: list[str], timeout: float, tag: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONSAFEPATH": "1",  # cwd is not implicitly on sys.path; PYTHONPATH below is explicit
        "PYTHONPATH": str(repo),
    }
    with tempfile.TemporaryDirectory(prefix=f"grade-{tag}-") as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider", "-q",
               "-o", "junit_family=xunit2", f"--junitxml={junit}", "--rootdir", str(repo)]
        # Record the stable part of the command only: the junit path lives in a
        # per-run temp dir and must not make two identical runs look different.
        result = {"cmd": "pytest " + " ".join(args), "timed_out": False, "returncode": None}
        try:
            proc = run(cmd, cwd=repo, timeout=timeout, env=env)
            result["returncode"] = proc.returncode
            result["stdout_tail"] = proc.stdout[-3000:]
            result["stderr_tail"] = proc.stderr[-2000:]
        except subprocess.TimeoutExpired as e:
            result["timed_out"] = True
            out = e.stdout or ""
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            result["stdout_tail"] = out[-3000:]
        result.update(parse_junit(junit))
    return result


# --------------------------------------------------------------------------- main
def grade(args) -> dict:
    repo, patch, tests_dir = Path(args.repo), Path(args.patch), Path(args.tests)
    manifest = json.loads(Path(args.manifest).read_text())
    report = {
        "grader_version": 1,
        "started_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "repo_commit": None,
        "score": 0.0,
        "hidden_pass_fraction": 0.0,
        "verdict": None,
        "policy": {"violations": [], "warnings": []},
        "apply": None,
        "hidden": None,
        "regression": None,
    }
    if not patch.is_file():
        raise GraderError(f"patch file not found: {patch}")

    reset_repo(repo)
    report["repo_commit"] = git(repo, "rev-parse", "HEAD").stdout.strip()
    expected_commit = manifest.get("repo_commit")
    if expected_commit and expected_commit != report["repo_commit"]:
        raise GraderError(f"repo at {report['repo_commit']} but manifest pinned {expected_commit}")

    try:
        if not check_patch_policy(repo, patch, report):
            report["verdict"] = "rejected_by_policy"
            return report
        scan_patch_text(patch, report)
        if not apply_patch(repo, patch, report):
            report["verdict"] = ("rejected_by_policy" if report["policy"]["violations"]
                                 else "patch_does_not_apply")
            return report

        hidden_files = install_hidden_tests(repo, tests_dir, manifest, report)
        hidden = run_pytest(repo, hidden_files, args.timeout, "hidden")
        report["hidden"] = hidden
        expected_n = int(manifest["hidden_test_count"])
        hidden_ok = (
            not hidden["timed_out"]
            and hidden["tests"] == expected_n
            and hidden["failures"] == 0
            and hidden["errors"] == 0
            and hidden["skipped"] == 0
        )
        passed = max(0, hidden["tests"] - hidden["failures"] - hidden["errors"] - hidden["skipped"])
        report["hidden_pass_fraction"] = round(passed / expected_n, 4) if expected_n else 0.0
        if hidden["tests"] != expected_n and not hidden["timed_out"]:
            report["hidden"]["count_mismatch"] = (
                f"collected {hidden['tests']} hidden tests, manifest expects {expected_n}")

        ignore = [f"--ignore={f}" for f in hidden_files]
        regression = run_pytest(repo, ["tests", *ignore], args.timeout, "regression")
        report["regression"] = regression
        base = manifest["baseline_suite"]
        regression_ok = (
            not regression["timed_out"]
            and regression["failures"] == 0
            and regression["errors"] == 0
            and regression["tests"] == int(base["tests"])
            and regression["skipped"] == int(base["skipped"])
        )
        if regression["tests"] != int(base["tests"]) and not regression["timed_out"]:
            report["regression"]["count_mismatch"] = (
                f"collected {regression['tests']} baseline tests, manifest expects {base['tests']}")

        if hidden_ok and regression_ok:
            report["score"] = 1.0
            report["verdict"] = "pass"
        elif not hidden_ok:
            report["verdict"] = "hidden_tests_failed"
        else:
            report["verdict"] = "regression_in_existing_suite"
        return report
    finally:
        if not args.keep:
            try:
                reset_repo(repo)
            except GraderError as e:  # pragma: no cover
                report.setdefault("grader_warnings", []).append(str(e))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="/work/repo")
    ap.add_argument("--patch", required=True, help="candidate unified diff (empty file = baseline)")
    ap.add_argument("--tests", default="/grader/hidden_tests")
    ap.add_argument("--manifest", default="/grader/manifest.json")
    ap.add_argument("--out", default="-", help="report path, or - for stdout")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds per pytest run")
    ap.add_argument("--keep", action="store_true", help="leave the patched repo in place")
    args = ap.parse_args(argv)
    try:
        report = grade(args)
    except GraderError as e:
        sys.stderr.write(f"GRADER ERROR: {e}\n")
        return 2
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
