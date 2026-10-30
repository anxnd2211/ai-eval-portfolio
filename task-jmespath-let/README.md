# Task: add `let` expressions to jmespath.py

A complete, reproducible SWE-bench-style evaluation task: a real repository
pinned by commit, a specification-grade problem statement, hidden tests that
exercise only the public API, a tamper-resistant grader, seven negative
controls, and a validation script that proves fail-before / pass-after /
reject-cheats / deterministic in one command.

| | |
|---|---|
| Repository | [jmespath/jmespath.py](https://github.com/jmespath/jmespath.py) @ `2812594e` (MIT, ~1.7k LOC, no runtime deps) |
| Feature | JEP-11a lexical scoping: `let $x = expr, ... in expr` and `$x` references |
| Files the gold patch touches | `lexer.py`, `parser.py`, `ast.py`, `visitor.py`, `exceptions.py` (+ changelog) — a genuinely cross-module change |
| Hidden tests | 74, public API only, every one traceable to a clause in `problem.md` |
| Regression guard | the repo's own 992-test suite must stay green |
| Validation | `./run_checks.sh` → `ALL CHECKS PASSED` (see `CALIBRATION.md` for numbers) |

## Layout

```
problem.md              what the agent sees
Dockerfile              pinned base image (by digest) + pinned repo commit + pinned pytest
context/jmespath/       pristine source snapshot (for chat-mode agents and human reviewers)
solution.patch          gold patch, 115 insertions / 17 deletions
grader/grade.py         tamper-resistant grader (stdlib only)
grader/make_manifest.py builds manifest.json (test hashes, counts, pinned commit)
grader/hidden_tests/    test_let.py
negatives/              7 near-miss and cheating patches that must score 0
run_checks.sh           end-to-end validation in Docker
CALIBRATION.md          measured results + the agent-calibration protocol
```

## Quick start (about 2 minutes, needs Docker)

```bash
./run_checks.sh
```

Grade any candidate patch yourself:

```bash
docker run --rm --network none \
  --mount type=bind,src=$PWD/grader,dst=/grader,readonly \
  --mount type=bind,src=$PWD/solution.patch,dst=/patch.diff,readonly \
  jmespath-let-env:local python /grader/grade.py --patch /patch.diff | jq '{verdict, score, hidden_pass_fraction}'
```

## Design decisions

**Why this repository and feature.** Small enough to review in an afternoon,
real enough to matter (jmespath is the query language behind the AWS CLI's
`--query`). The feature is cross-cutting: a new token in the lexer, a
contextual keyword and a new grammar production in a Pratt parser, two AST
node types, a scope stack in the interpreter, a new exception. It cannot be
solved by editing one function, and the "99% right" implementations are
numerous (see negatives), which is what makes a task discriminating.

**Why the spec is public.** A reviewer can check every test against JEP-11a in
minutes. The trade-off is contamination risk, which disqualifies the task for
a frontier benchmark; `CALIBRATION.md` explains the distinction. Novelty
screening is a separate skill from harness craft, and this package is about
harness craft.

**Test design rules** (enforced while writing `test_let.py`):

1. Public API only. No AST shapes, no token names, no private attributes. An
   agent that stores scopes as a linked list or names the token `varref` passes.
2. One clause, one test. Every behavioural sentence in `problem.md` has a test;
   every test maps to a sentence. "Gotcha" tests that the statement does not
   license are task bugs.
3. Separate compile-time from evaluation-time errors, because the spec does.
4. Include regression guards for behaviour that must *not* change (`let`/`in`
   as identifiers, `=` outside bindings, projections).
5. Keep data small and literal so a failing test is self-explanatory.

**Grader threat model.**

| Threat | Control |
|---|---|
| Patch edits or deletes tests, adds `conftest.py`, pytest/packaging config, dotfiles | Path allowlist on `git apply --numstat`, re-checked post-apply; renames/deletes/mode changes/binaries rejected; verdict `rejected_by_policy` before any test runs |
| Hidden tests tampered with in transit | SHA-256 manifest verified before copy; mounted read-only |
| Tests deselected, collection crashed, `pytest.skip` abuse | Collected count must equal the manifest; `skipped > 0` fails |
| Existing behaviour broken | Full pre-existing suite must match baseline counts with 0 failures |
| Network / toolchain tampering | `--network none`, non-root user, pinned image digest |
| Non-determinism | Hash seed fixed, bytecode off, volatile fields excluded; `run_checks.sh` diffs two gold runs |
| Suspicious but legal code (`import pytest` in the library, `sys.modules`, subprocess) | Reported as warnings for human review; never auto-rejected, to avoid false positives |

**Reward shape.** `score` is binary because partial credit rewards the
near-misses that matter least (see the `hidden_pass` column in
`CALIBRATION.md`: four of five wrong patches would get 0.9+). The pass
fraction is still reported for curriculum/analysis use.

## Adapting the harness to another repository

1. Pin: base image digest, repo commit (`git tag task-base`), test runner version.
2. Write `problem.md` from the spec *before* the tests; then write tests only
   from `problem.md`.
3. Write the gold patch last, so the statement is not shaped by one solution.
4. Edit `ALLOWED_*`/`DENIED_*` in `grade.py` for the repo's layout; everything
   else is repo-agnostic.
5. Produce 3 to 7 negatives from the gold patch. If a negative passes, the
   tests are under-specified; if the gold fails, the statement is wrong.
6. Run `run_checks.sh`, then calibrate with real agents (`CALIBRATION.md`).
