"""Tests for the verifiers wrapper. Docker-dependent tests skip cleanly if the
task image is not built (run task-jmespath-let/run_checks.sh first)."""
import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
import verifiers.v1 as vf

from jmespath_let_env import JmespathLetTaskset, extract_patch
from jmespath_let_env.taskset import JmespathLetTasksetConfig

TASK_DIR = Path(__file__).resolve().parents[2] / "task-jmespath-let"
IMAGE = "jmespath-let-env:local"


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE],
                          capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ready(), reason="task image not available")


def make_task():
    taskset = JmespathLetTaskset(JmespathLetTasksetConfig())
    tasks = list(taskset)
    assert len(tasks) == 1
    return tasks[0]


def make_trace(task, reply: str) -> vf.Trace:
    return vf.Trace(
        task=vf.TraceTask(type=type(task).__name__, data=task.data),
        agent=vf.AgentInfo(config=vf.AgentConfig()),
        root_reply=reply,
    )


def score(task, reply: str) -> vf.Trace:
    trace = make_trace(task, reply)
    asyncio.run(task.score(trace))
    return trace


def test_taskset_yields_the_task_with_spec_and_sources_in_prompt():
    task = make_task()
    prompt = task.data.prompt_text
    assert "let-expression" in prompt and "UndefinedVariable" in prompt
    assert "### `jmespath/parser.py`" in prompt
    assert "```diff" in prompt  # reply-format instructions
    assert task.data.network_allow == [] and task.data.image == IMAGE
    assert set(task.data.sources_included) >= {"jmespath/lexer.py", "jmespath/visitor.py"}


def test_extract_patch_prefers_last_fenced_block_and_accepts_bare_diffs():
    gold = (TASK_DIR / "solution.patch").read_text()
    assert extract_patch(f"first try\n```diff\nold\n```\nactually:\n```diff\n{gold}```") == gold
    assert extract_patch("```patch\n" + gold + "```") == gold
    assert extract_patch("Here you go:\n\n" + gold) == gold
    assert extract_patch("I could not solve this.") is None
    assert extract_patch(None) is None


def test_reply_without_patch_scores_zero_without_docker():
    task = make_task()
    trace = score(task, "Sorry, no patch.")
    assert trace.rewards["hidden_tests_pass"].score == 0.0
    assert trace.metrics["patch_found"] == 0.0
    assert trace.info["grader"]["verdict"] == "no_patch_in_reply"


@needs_docker
def test_gold_patch_scores_one():
    task = make_task()
    gold = (TASK_DIR / "solution.patch").read_text()
    trace = score(task, "Implemented per JEP-11a.\n\n```diff\n" + gold + "```\n")
    assert trace.rewards["hidden_tests_pass"].score == 1.0
    assert trace.metrics["hidden_pass_fraction"] == 1.0
    assert trace.metrics["policy_violations"] == 0.0
    assert trace.info["grader"]["verdict"] == "pass"


@needs_docker
def test_cheating_patch_is_rejected_by_policy():
    task = make_task()
    cheat = (TASK_DIR / "negatives" / "cheat_conftest_skips_everything.patch").read_text()
    trace = score(task, "```diff\n" + cheat + "```")
    assert trace.rewards["hidden_tests_pass"].score == 0.0
    assert trace.metrics["policy_violations"] >= 1.0
    assert trace.info["grader"]["verdict"] == "rejected_by_policy"
