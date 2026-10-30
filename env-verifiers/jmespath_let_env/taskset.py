"""verifiers v1 taskset for the jmespath.py let-expressions task.

Mode implemented here: **patch-in-reply**. The agent receives the problem
statement plus a pristine snapshot of the library sources and must answer with
one unified diff in a fenced ```diff block. Scoring hands that diff to the
task's own grader (``task-jmespath-let/grader/grade.py``) running inside the
pinned Docker image with networking disabled. The grader's binary ``score`` is
the reward; its ``hidden_pass_fraction`` and policy counters are metrics.

Why reuse the grader instead of re-implementing scoring in Python here: the
grader is the single source of truth for what "correct" means (path policy,
hidden-test hashes, regression guard, determinism). Two graders drift; one
does not.

Container mode (agent works inside the image with a bash harness) is the
natural next step and uses the same grader: ``TaskData.image``/``workdir`` are
already set for it, ``stage_verifier()`` would ``runtime.write()`` the grader
and hidden tests into the box after the agent finishes, and a runtime-bound
``@vf.reward`` would run ``git diff`` + ``grade.py`` there. It is not wired
up in this package because it could not be exercised end to end in the
authoring environment, and untested scoring code is worse than none.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

import verifiers.v1 as vf

HERE = Path(__file__).resolve().parent
DEFAULT_TASK_DIR = (HERE.parent.parent / "task-jmespath-let").resolve()

REPLY_FORMAT = """

## How to answer

Reply with exactly one fenced code block tagged `diff` containing a unified
diff against the repository root (the format produced by `git diff`, with
`a/` and `b/` prefixes). Example skeleton:

```diff
diff --git a/jmespath/lexer.py b/jmespath/lexer.py
--- a/jmespath/lexer.py
+++ b/jmespath/lexer.py
@@ -10,3 +10,4 @@
 ...
```

Only files under `jmespath/` (plus `CHANGELOG.rst`/`README.rst`) may change.
The patch is applied to a clean checkout and graded automatically; text
outside the code block is ignored.
"""

_FENCE = re.compile(r"```(?:diff|patch)[^\n]*\n(.*?)\n```", re.DOTALL)
_BARE_HEADER = re.compile(r"^(diff --git .*|--- (?:a/|/dev/null).*)$", re.MULTILINE)


def extract_patch(text: str | None) -> str | None:
    """Return the unified diff contained in an agent reply, or ``None``.

    Accepts a fenced ```diff / ```patch block (the last one wins, so a model
    that revises its answer is graded on the revision) or a bare reply that
    starts at a ``diff --git`` / ``--- a/`` header. Anything else is "no patch".
    """
    text = text or ""
    blocks = _FENCE.findall(text)
    if blocks:
        return blocks[-1].strip("\n") + "\n"
    m = _BARE_HEADER.search(text)
    if m:
        return text[m.start():].strip("\n") + "\n"
    return None


def run_grader(patch_text: str, task_dir: Path, image: str, timeout: float) -> dict:
    """Grade one patch with the task's own grader inside the pinned image."""
    grader_dir = (task_dir / "grader").resolve()
    if not (grader_dir / "manifest.json").is_file():
        raise RuntimeError(
            f"{grader_dir}/manifest.json missing: run task-jmespath-let/run_checks.sh once")
    with tempfile.TemporaryDirectory(prefix="jmespath-let-") as tmp:
        patch = Path(tmp) / "candidate.diff"
        patch.write_text(patch_text)
        cmd = [
            "docker", "run", "--rm", "--network", "none", "--cpus", "2", "--memory", "2g",
            "--mount", f"type=bind,src={grader_dir},dst=/grader,readonly",
            "--mount", f"type=bind,src={patch},dst=/patch.diff,readonly",
            image, "python", "/grader/grade.py", "--patch", "/patch.diff",
            "--timeout", str(timeout),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 2 + 120)
    if proc.returncode != 0:
        raise RuntimeError(f"grader exited {proc.returncode}: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


class JmespathLetData(vf.TaskData):
    """One row: the problem statement (plus source snapshot) as the prompt."""

    sources_included: list[str] = []
    """Repo-relative paths of the source files inlined into the prompt."""


class JmespathLetTaskConfig(vf.TaskConfig):
    task_dir: str = str(DEFAULT_TASK_DIR)
    """Directory holding problem.md, context/, grader/ (the task package)."""
    image: str = "jmespath-let-env:local"
    """Docker image built from task_dir/Dockerfile."""
    grader_timeout: float = 300.0
    """Seconds allowed per pytest run inside the grader."""


_NO_PATCH = {
    "score": 0.0,
    "hidden_pass_fraction": 0.0,
    "verdict": "no_patch_in_reply",
    "policy": {"violations": [], "warnings": []},
}


class JmespathLetTask(vf.Task[JmespathLetData, vf.State, JmespathLetTaskConfig]):
    """Reward = the grader's binary score; metrics expose the grader's detail."""

    def __init__(self, data: JmespathLetData, config: JmespathLetTaskConfig | None = None) -> None:
        super().__init__(data, config)
        self._reports: dict[str, asyncio.Future] = {}

    async def _report(self, trace: vf.Trace) -> dict:
        # Rewards and metrics are invoked concurrently; grade each trace once.
        fut = self._reports.get(trace.id)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._reports[trace.id] = fut
            try:
                patch = extract_patch(trace.last_reply)  # property, not a method
                if patch is None:
                    report = dict(_NO_PATCH)
                else:
                    report = await asyncio.to_thread(
                        run_grader, patch, Path(self.config.task_dir),
                        self.config.image, self.config.grader_timeout)
                trace.info["grader"] = {
                    "verdict": report.get("verdict"),
                    "policy_violations": list(report.get("policy", {}).get("violations", [])),
                    "policy_warnings": list(report.get("policy", {}).get("warnings", [])),
                    "failed_hidden_tests": list((report.get("hidden") or {}).get("failed_ids", []))[:20],
                }
                fut.set_result(report)
            except BaseException as e:  # propagate to every awaiter
                fut.set_exception(e)
                raise
        return await fut

    @vf.reward(weight=1.0)
    async def hidden_tests_pass(self, trace: vf.Trace) -> float:
        return float((await self._report(trace))["score"])

    @vf.metric
    async def hidden_pass_fraction(self, trace: vf.Trace) -> float:
        return float((await self._report(trace))["hidden_pass_fraction"])

    @vf.metric
    async def patch_found(self, trace: vf.Trace) -> float:
        return 0.0 if (await self._report(trace))["verdict"] == "no_patch_in_reply" else 1.0

    @vf.metric
    async def policy_violations(self, trace: vf.Trace) -> float:
        return float(len((await self._report(trace))["policy"]["violations"]))


class JmespathLetTasksetConfig(vf.TasksetConfig):
    task: JmespathLetTaskConfig = JmespathLetTaskConfig()
    problem_file: str = "problem.md"
    include_sources: bool = True
    """Inline the pristine jmespath/*.py snapshot (context/) into the prompt so a
    chat-only agent can write a patch without a filesystem."""


class JmespathLetTaskset(vf.Taskset[JmespathLetTask, JmespathLetTasksetConfig]):
    def load(self) -> Iterable[JmespathLetTask]:
        task_dir = Path(self.config.task.task_dir)
        prompt = (task_dir / self.config.problem_file).read_text()
        included: list[str] = []
        if self.config.include_sources:
            parts = ["\n\n## Repository sources at the pinned commit\n"]
            for src in sorted((task_dir / "context" / "jmespath").glob("*.py")):
                rel = f"jmespath/{src.name}"
                included.append(rel)
                parts.append(f"\n### `{rel}`\n\n```python\n{src.read_text()}\n```\n")
            prompt += "".join(parts)
        prompt += REPLY_FORMAT
        data = JmespathLetData(
            id="jmespath-let-expressions",
            name="jmespath.py: add let-expressions (JEP-11a lexical scoping)",
            description="Cross-file language feature (lexer, parser, AST, interpreter, "
                        "exceptions) graded by hidden public-API tests inside a pinned "
                        "Docker image.",
            prompt=prompt,
            image=self.config.task.image,
            workdir="/work/repo",
            agent_user="runner",
            network_allow=[],
            sources_included=included,
        )
        yield JmespathLetTask(data, self.config.task)
