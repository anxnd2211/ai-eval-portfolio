# Calibration notes

## What was measured here (reproducible with `./run_checks.sh`)

| Check | Result |
|---|---|
| Hidden tests collected | 74 |
| Pre-existing suite collected (regression guard) | 992 (1 skipped at baseline: a Python 2 `maxint` test) |
| Baseline (no patch) hidden pass fraction | 0.338 |
| Gold patch | 74/74 hidden, 992/992 existing, score 1.0, 0 policy warnings |
| Negative controls | 7/7 score 0.0 (table below) |
| Determinism | two gold runs produce byte-identical reports after stripping timestamps and log tails |

The baseline pass fraction is deliberately non-zero. About a third of the hidden
tests are *regression guards* for behaviour the task says must keep working
(`let` and `in` as ordinary identifiers, `=` outside a binding still a syntax
error, `foo[*][0]` still projecting). They pass before the patch and must still
pass after it. The grader requires 74/74, so they contribute nothing to a
candidate that breaks them, which is the point.

## Negative controls

Each is the gold patch with one deliberate defect. All score 0; the grader's
verdict pinpoints why. `hidden_pass` is the fraction a lenient grader would
have awarded, which shows why pass-fraction alone is not a safe signal here.

| Patch | Defect | Verdict | hidden_pass |
|---|---|---|---|
| `wrong_sequential_bindings` | later bindings can see earlier ones in the same `let` | 1 hidden test fails (`sibling_binding_not_visible_in_same_let`) | 0.987 |
| `wrong_scope_leak` | bindings never popped after the body | shadowing/visibility tests fail | 0.932 |
| `wrong_let_reserved_keyword` | `let` treated as a reserved word | identifier-compat tests fail | 0.905 |
| `wrong_compile_time_undefined_check` | undefined `$x` rejected at compile time | lazy-evaluation and error-timing tests fail | 0.905 |
| `wrong_undefined_returns_null` | unbound `$x` evaluates to `null` | every undefined-variable test fails | 0.878 |
| `cheat_conftest_skips_everything` | root `conftest.py` auto-skips all tests | rejected by path policy, no test run | 0.0 |
| `cheat_edits_existing_tests` | relaxes an assertion in `tests/test_compliance.py` | rejected by path policy, no test run | 0.0 |

The first five are the "99% right" implementations a strong model is most
likely to produce. A grader that awarded partial credit would call four of them
a near-pass; the binary score plus the named failing test is what makes the
signal useful for training and for review.

## What was NOT measured here

Frontier-agent pass rates. No model API was available in the authoring
environment, so the difficulty bar has not been empirically set. The protocol
I use when it is:

1. Run each of three agents (one frontier, two cheaper) 5 times each from the
   pinned image with `problem.md` as the only instruction and a 60-minute wall
   clock, network disabled.
2. Grade every rollout with `grade.py`; record `verdict`, `hidden_pass_fraction`
   and the `failed_ids` list.
3. Target band for a *hard* SWE task: the strongest agent passes 10 to 25% of
   rollouts, the others rarely or never. Above that, raise the bar by adding a
   spec clause the agents consistently miss (candidates here: JEP-11a's
   "implementation-defined initial scope" via `Options`, or `let` inside
   `sort_by`/`min_by` key expressions). Below that, check whether failures are
   fair (spec says so, test asserts it) or over-specification.
4. Read every failing rollout's diff. A test that fails for reasons the problem
   statement does not state is a bug in the task, not in the agent.

## Why this task is public-spec'd

The feature follows a published specification (JEP-11a) on purpose: a reviewer
can verify every assertion against an independent source in minutes. For a
benchmark that must resist contamination I would not choose a publicly
specified feature; the novelty, originality and "no public solution" screening
are a separate discipline from the harness craft this package demonstrates.
