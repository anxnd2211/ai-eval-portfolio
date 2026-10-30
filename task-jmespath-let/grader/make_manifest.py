#!/usr/bin/env python3
"""Build manifest.json for grade.py.

Run inside the task image with the hidden tests mounted:

    python /grader/make_manifest.py --tests /grader/hidden_tests > manifest.json

Records: pinned repo commit, SHA-256 of every hidden test file, the number of
hidden tests pytest collects, and the baseline suite's collected/skipped counts
(the pinned suite is green, so passed == tests - skipped).
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def junit_counts(repo: Path, args: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "j.xml"
        subprocess.run([sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider", "-q",
                        "-o", "junit_family=xunit2", f"--junitxml={junit}"],
                       cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        root = ET.parse(junit).getroot()
        cases = list(root.iter("testcase"))
        return {
            "tests": len(cases),
            "failures": sum(c.find("failure") is not None for c in cases),
            "errors": sum(c.find("error") is not None for c in cases),
            "skipped": sum(c.find("skipped") is not None for c in cases),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/work/repo")
    ap.add_argument("--tests", default="/grader/hidden_tests")
    a = ap.parse_args()
    repo, tests = Path(a.repo), Path(a.tests)
    subprocess.run(["git", "-C", str(repo), "reset", "-q", "--hard", "task-base"], check=True)
    subprocess.run(["git", "-C", str(repo), "clean", "-fdxq"], check=True)
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                            stdout=subprocess.PIPE, text=True).stdout.strip()
    hidden = {}
    installed = []
    for f in sorted(tests.glob("test_*.py")):
        rel = f"tests/{f.name}"
        hidden[rel] = sha256(f)
        shutil.copyfile(f, repo / rel)
        installed.append(rel)
    # Collect-only is enough for the count; hidden tests fail at baseline by design.
    collect = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                              "-p", "no:cacheprovider", *installed],
                             cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    hidden_count = sum(1 for line in collect.stdout.splitlines() if "::" in line)
    baseline = junit_counts(repo, ["tests", *[f"--ignore={r}" for r in installed]])
    for rel in installed:
        (repo / rel).unlink()
    if baseline["failures"] or baseline["errors"]:
        sys.stderr.write(f"baseline suite is not green: {baseline}\n")
        return 2
    manifest = {
        "repo_commit": commit,
        "hidden_tests": hidden,
        "hidden_test_count": hidden_count,
        "baseline_suite": baseline,
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
