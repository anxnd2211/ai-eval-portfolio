#!/usr/bin/env bash
# End-to-end validation of the task package. Requires Docker.
#
#   ./run_checks.sh            build image, (re)build manifest, run all checks
#   ./run_checks.sh --no-build reuse the existing image
#
# Checks (all must hold for the task to be considered sound):
#   1. baseline  : empty patch -> score 0, and hidden tests mostly fail
#   2. gold      : solution.patch -> score 1, full existing suite still green
#   3. negatives : every patch in negatives/ -> score 0, with a precise verdict
#   4. determinism: gold graded twice -> identical reports (volatile fields stripped)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="${IMG:-jmespath-let-env:local}"
OUT="$HERE/.reports"; mkdir -p "$OUT"

if [[ "${1:-}" != "--no-build" ]]; then
  echo "== building image $IMG"
  docker build -q -t "$IMG" "$HERE" >/dev/null
fi

grade() { # grade <patch-file> <report-name>
  docker run --rm --network none --cpus 2 --memory 2g \
    --mount "type=bind,src=$HERE/grader,dst=/grader,readonly" \
    --mount "type=bind,src=$1,dst=/patch.diff,readonly" \
    "$IMG" python /grader/grade.py --patch /patch.diff --timeout 300 > "$OUT/$2.json"
}
field() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$1" "$2"; }

echo "== building manifest"
docker run --rm --network none \
  --mount "type=bind,src=$HERE/grader/hidden_tests,dst=/grader/hidden_tests,readonly" \
  --mount "type=bind,src=$HERE/grader/make_manifest.py,dst=/grader/make_manifest.py,readonly" \
  "$IMG" python /grader/make_manifest.py > "$HERE/grader/manifest.json"
echo "   hidden tests: $(field "$HERE/grader/manifest.json" "d['hidden_test_count']")   baseline suite: $(field "$HERE/grader/manifest.json" "d['baseline_suite']")"

fail=0
echo "== 1. baseline (empty patch)"
: > "$OUT/empty.patch"; grade "$OUT/empty.patch" baseline
echo "   verdict=$(field "$OUT/baseline.json" "d['verdict']") hidden_pass_fraction=$(field "$OUT/baseline.json" "d['hidden_pass_fraction']")"
[[ "$(field "$OUT/baseline.json" "d['score']")" == "0.0" ]] || { echo "   FAIL: baseline must score 0"; fail=1; }

echo "== 2. gold (solution.patch)"
grade "$HERE/solution.patch" gold
echo "   verdict=$(field "$OUT/gold.json" "d['verdict']") hidden=$(field "$OUT/gold.json" "d['hidden']['tests']") regression=$(field "$OUT/gold.json" "d['regression']['tests']") warnings=$(field "$OUT/gold.json" "len(d['policy']['warnings'])")"
[[ "$(field "$OUT/gold.json" "d['score']")" == "1.0" ]] || { echo "   FAIL: gold must score 1"; fail=1; }

echo "== 3. negatives"
for p in "$HERE"/negatives/*.patch; do
  name="$(basename "$p" .patch)"; grade "$p" "neg-$name"
  score="$(field "$OUT/neg-$name.json" "d['score']")"
  detail="$(field "$OUT/neg-$name.json" "(d['policy']['violations'][:1] or d['hidden'] and d['hidden']['failed_ids'][:3] or [])")"
  printf '   %-40s score=%s verdict=%s hidden_pass=%s\n      %s\n' "$name" "$score" "$(field "$OUT/neg-$name.json" "d['verdict']")" "$(field "$OUT/neg-$name.json" "d['hidden_pass_fraction']")" "$detail"
  [[ "$score" == "0.0" ]] || { echo "   FAIL: $name must score 0"; fail=1; }
done

echo "== 4. determinism (gold twice)"
grade "$HERE/solution.patch" gold2
python3 - "$OUT/gold.json" "$OUT/gold2.json" <<'PY' || fail=1
import json, sys
def strip(d):
    d = json.loads(json.dumps(d))
    d.pop("started_utc", None)
    for k in ("hidden", "regression"):
        if d.get(k):
            for v in ("stdout_tail", "stderr_tail"):
                d[k].pop(v, None)
    return d
a, b = (strip(json.load(open(p))) for p in sys.argv[1:])
if a != b:
    print("   FAIL: reports differ"); sys.exit(1)
print("   identical")
PY

echo
[[ $fail -eq 0 ]] && echo "ALL CHECKS PASSED" || { echo "SOME CHECKS FAILED (see $OUT)"; exit 1; }
