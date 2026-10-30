# AI evaluation portfolio

Public, reproducible evidence that I can author, harden and calibrate the
tasks AI labs use to evaluate and train coding agents.

## Two-minute review path

```bash
cd task-jmespath-let && ./run_checks.sh        # needs Docker
```

Expected final line: `ALL CHECKS PASSED`. Along the way you will see the gold
patch pass 74 hidden tests with the repository's own 992-test suite still
green, seven deliberately wrong or cheating patches score zero with a precise
verdict each, and two identical grading reports.

Then read, in order: `task-jmespath-let/problem.md` (the agent-facing spec),
`task-jmespath-let/grader/hidden_tests/test_let.py` (how tests trace to the
spec), `task-jmespath-let/grader/grade.py` (the threat model, in code),
`task-jmespath-let/CALIBRATION.md` (what was and was not measured).

## Contents

| Folder | What it is |
|---|---|
| `task-jmespath-let/` | A complete SWE-bench-style task on a real repository: pinned Dockerfile, problem statement, hidden tests, tamper-resistant grader, negative controls, validation script, calibration notes |
| `env-verifiers/` | The same task wrapped as a [verifiers](https://github.com/PrimeIntellect-ai/verifiers) v1 taskset (reward + metrics) with its own tests, reusing the task's grader so there is one definition of "correct" |
| `applications/` | Private, git-ignored: platform-by-platform application notes |

## What this demonstrates

- Specification first: the statement was written from a public spec (JEP-11a),
  the tests from the statement, the gold patch last.
- Fair tests: public API only, one clause per test, compile-time vs
  evaluation-time errors separated, regression guards included.
- Un-gameable grading: path allowlist, hash-verified hidden tests, collected
  count checks, skip detection, regression guard, no network, non-root,
  deterministic reports.
- Honest calibration: negatives show why binary scoring is right; the
  agent-calibration protocol is documented and marked as not yet run here.

## Publishing

```bash
gh repo create ai-eval-portfolio --public --source=. --push
```

`applications/` is git-ignored, so it never leaves this machine. CI
(`.github/workflows/validate.yml`) rebuilds the image and re-runs every check
on each push; the badge below turns green on the first run.

![validate task](https://github.com/anxnd2211/ai-eval-portfolio/actions/workflows/validate.yml/badge.svg)

Hidden tests are public in this repo by design; the task is a portfolio piece,
not a held-out benchmark (see `CALIBRATION.md`, "Why this task is public-spec'd").
