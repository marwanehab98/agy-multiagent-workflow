#!/usr/bin/env bash
# Exercises the skill's git and state scripts in a throwaway repo. Run: bash tests/test_scripts.sh
set -u
SK=$(cd "$(dirname "$0")/../scripts" && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/omwtest.XXXX"); cd "$T"
pass=0; fail=0
ok()  { echo "PASS $1"; pass=$((pass+1)); }
bad() { echo "FAIL $1"; fail=$((fail+1)); }
expect() { local want=$1 got=$2 name=$3; [ "$want" = "$got" ] && ok "$name" || bad "$name (want exit $want, got $got)"; }

git init -q --bare -b main origin.git
git init -q -b main repo && cd repo
git config user.email t@t; git config user.name t
echo a > a.txt; printf 'out/\n' > .gitignore; git add .; git commit -qm init
git remote add origin ../origin.git; git push -q origin main
BASE=$(git rev-parse HEAD)

git worktree add -q ../docs -b T-docs "$BASE"
echo "--- check-tree catches an uncommitted docs file"
mkdir -p ../docs/docs/plans; echo findings > ../docs/docs/plans/egm-review.md
bash "$SK/check-tree.sh" ../docs "$BASE" >/dev/null; expect 2 $? "check-tree flags uncommitted docs file before dispatch"
rm ../docs/docs/plans/egm-review.md

echo "--- check-tree: renames and spaces"
git -C ../docs mv a.txt "b file.txt"
out=$(bash "$SK/check-tree.sh" ../docs "$BASE"); rc=$?
expect 2 $rc "rename detected"; echo "$out" | grep -q 'b file.txt' && ok "unquoted path with space" || bad "path with space: $out"
git -C ../docs reset -q --hard "$BASE"
bash "$SK/check-tree.sh" ../docs "$BASE" >/dev/null; expect 0 $? "clean tree"

echo "--- start-point"
out=$(bash "$SK/start-point.sh" . main); echo "$out" | grep -q "START $BASE up-to-date" && ok "up-to-date" || bad "up-to-date: $out"
git clone -q ../origin.git ../other && (cd ../other && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m remote && git push -q origin main)
out=$(bash "$SK/start-point.sh" . main); R=$(git rev-parse origin/main)
echo "$out" | grep -q "START $R local-behind" && ok "local-behind uses origin" || bad "local-behind: $out"
git commit -q --allow-empty -m local
bash "$SK/start-point.sh" . main >/dev/null; expect 3 $? "diverged refused"
git reset -q --hard "$R"; BASE=$R

echo "--- merge-task (update-ref, no checkout)"
git branch feature "$BASE"
git worktree add -q ../impl -b T-impl feature
(cd ../impl && echo x > x.txt && git add x.txt && git -c user.email=t@t -c user.name=t commit -qm task1)
H1=$(git -C ../impl rev-parse HEAD)
bash "$SK/merge-task.sh" . feature "$BASE" "$H1" >/dev/null; expect 0 $? "ff without checkout"
[ "$(git rev-parse feature)" = "$H1" ] && ok "branch moved" || bad "branch moved"
[ "$(git branch --show-current)" = "main" ] && ok "main checkout untouched" || bad "main checkout untouched"
bash "$SK/merge-task.sh" . feature "$BASE" "$H1" >/dev/null; expect 3 $? "stale expected_old refused"
git branch side "$BASE"; (cd ../impl && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m t2); H2=$(git -C ../impl rev-parse HEAD)
bash "$SK/merge-task.sh" . feature "$H1" "$(git rev-parse side)" >/dev/null; expect 3 $? "non-ff target refused"
git switch -q feature
bash "$SK/merge-task.sh" . feature "$H1" "$H2" >/dev/null; expect 2 $? "refused while checked out"
git switch -q main
bash "$SK/branch-at.sh" . feature "$H1" >/dev/null; expect 0 $? "branch-at still at H1"

echo "--- gate"
cat > ../rv.txt <<'EOF'
FEATURE_BRANCH=feature
VERIFY_1=test -f x.txt
VERIFY_2=mkdir -p out build && touch out/cache build/artifact && echo built
EOF
python3 -I "$SK/gate.py" --run-values ../rv.txt --worktree ../impl --head "$H2" --log ../gate1.log >/dev/null; expect 0 $? "gate passes (falls back to VERIFY)"
[ ! -e ../impl/build ] && ok "untracked build output removed" || bad "build output left"
[ -e ../impl/out/cache ] && ok "gitignored output kept" || bad "gitignored output removed"
bash "$SK/check-tree.sh" ../impl "$H2" >/dev/null; expect 0 $? "tree clean after gate"
echo 'GATE_1=false' >> ../rv.txt
python3 -I "$SK/gate.py" --run-values ../rv.txt --worktree ../impl --head "$H2" --log ../gate2.log >/dev/null; expect 1 $? "GATE_ lines take precedence and failure is reported"
grep -q "GATE_1 exit=1" ../gate2.log && ok "failure logged" || bad "failure logged"
python3 -I "$SK/gate.py" --run-values ../rv.txt --worktree ../impl --head "$H2" --log ../gate3.log --prefix VERIFY >/dev/null; expect 0 $? "--prefix VERIFY ignores GATE_"
python3 -I "$SK/gate.py" --run-values ../rv.txt --worktree ../impl --head "$H1" --log ../gate4.log >/dev/null; expect 2 $? "refuses when HEAD != head"

echo "--- state"
S=../state.json
python3 -I "$SK/state.py" --file $S set BASE_SHA_02=abc ARCHITECT_DISPATCH=ctx_1 >/dev/null
[ "$(python3 -I "$SK/state.py" --file $S get BASE_SHA_02)" = abc ] && ok "set/get" || bad "set/get"
python3 -I "$SK/state.py" --file $S incr FIX_ROUNDS_02 >/dev/null
[ "$(python3 -I "$SK/state.py" --file $S incr FIX_ROUNDS_02)" = 2 ] && ok "incr" || bad "incr"
python3 -I "$SK/state.py" --file $S get NOPE >/dev/null; expect 1 $? "missing key exit 1"
python3 -I "$SK/state.py" --file $S unset FIX_ROUNDS_02
python3 -I "$SK/state.py" --file $S show | grep -q FIX_ROUNDS && bad "unset" || ok "unset"

echo; echo "passed=$pass failed=$fail"; rm -rf "$T"; [ $fail -eq 0 ]
