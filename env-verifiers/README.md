# jmespath-let-env (verifiers v1 taskset)

Wraps `../task-jmespath-let` as a [verifiers](https://github.com/PrimeIntellect-ai/verifiers)
v1 taskset so the task can be used for evals and RL with the exact same grader
that validates the task package.

* **Prompt**: `problem.md` + a pristine snapshot of `jmespath/*.py` + reply-format rules.
* **Reward** `hidden_tests_pass` ∈ {0, 1}: the grader's binary verdict.
* **Metrics**: `hidden_pass_fraction`, `patch_found`, `policy_violations`; the
  grader's verdict and failing test ids land in `trace.info["grader"]`.
* **Isolation**: grading runs in the pinned image with `--network none`,
  hidden tests mounted read-only and hash-checked.

## Run

```bash
# once: build the image and the manifest
../task-jmespath-let/run_checks.sh

uv pip install -e . pytest
pytest tests -q                 # wrapper tests; Docker tests skip if the image is missing
uv run vf-eval jmespath-let-env -n 1 -r 4 --model <id>   # evaluate with a model
```

## Layout

- `jmespath_let_env/taskset.py` — `JmespathLetTaskset`, `JmespathLetTask`, `extract_patch`, `run_grader`.
- `tests/test_env.py` — loads the taskset, checks prompt assembly, scores a no-patch reply,
  the gold patch (score 1) and a cheating patch (policy rejection) through `Task.score`.

See the module docstring for why scoring delegates to the task's grader and how
to extend this to container mode.
