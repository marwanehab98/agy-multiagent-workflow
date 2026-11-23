#!/usr/bin/env bash
# Exercises the skill's git and state scripts in a throwaway repo. Run: bash tests/test_scripts.sh
set -u
SK=$(cd "$(dirname "$0")/../scripts" && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/amwtest.XXXX"); cd "$T"
trap 'cd /; rm -rf "$T"' EXIT  # also when a check aborts the script
export MISSION_CONTROL_WORKTREES="$T/wt-root"
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

echo "--- worktree.sh create & remove"
WT_DOCS=$(bash "$SK/worktree.sh" create . T-docs T-docs "$BASE")
expect 0 $? "worktree create docs"
[ -d "$WT_DOCS" ] && ok "docs worktree exists" || bad "docs worktree missing: $WT_DOCS"

echo "--- check-tree catches an uncommitted docs file"
mkdir -p "$WT_DOCS/docs/plans"; echo findings > "$WT_DOCS/docs/plans/flight-readiness.md"
bash "$SK/check-tree.sh" "$WT_DOCS" "$BASE" >/dev/null; expect 2 $? "check-tree flags uncommitted docs file before dispatch"
rm "$WT_DOCS/docs/plans/flight-readiness.md"

echo "--- check-tree: renames and spaces"
git -C "$WT_DOCS" mv a.txt "b file.txt"
out=$(bash "$SK/check-tree.sh" "$WT_DOCS" "$BASE"); rc=$?
expect 2 $rc "rename detected"; echo "$out" | grep -q 'b file.txt' && ok "unquoted path with space" || bad "path with space: $out"
git -C "$WT_DOCS" reset -q --hard "$BASE"
bash "$SK/check-tree.sh" "$WT_DOCS" "$BASE" >/dev/null; expect 0 $? "clean tree"

echo "--- start-point"
out=$(bash "$SK/start-point.sh" . main); echo "$out" | grep -q "START $BASE up-to-date" && ok "up-to-date" || bad "up-to-date: $out"
git clone -q ../origin.git ../other && (cd ../other && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m remote && git push -q origin main)
out=$(bash "$SK/start-point.sh" . main); R=$(git rev-parse origin/main)
echo "$out" | grep -q "START $R local-behind" && ok "local-behind uses origin" || bad "local-behind: $out"
git commit -q --allow-empty -m local
bash "$SK/start-point.sh" . main >/dev/null; expect 3 $? "diverged refused"
git reset -q --hard "$R"; BASE=$R
git checkout -q -b feat/addendum "$BASE"
git commit -q --allow-empty -m feat1
F1=$(git rev-parse HEAD)
out=$(bash "$SK/start-point.sh" . feat/addendum); echo "$out" | grep -q "START $F1 local-only" && ok "local-only feature branch" || bad "local-only feature branch: $out"
git push -q origin feat/addendum
out=$(bash "$SK/start-point.sh" . feat/addendum); echo "$out" | grep -q "START $F1 up-to-date" && ok "pushed feature branch up-to-date" || bad "pushed feature branch up-to-date: $out"
git commit -q --allow-empty -m feat2
F2=$(git rev-parse HEAD)
out=$(bash "$SK/start-point.sh" . feat/addendum); echo "$out" | grep -q "START $F2 local-ahead" && ok "feature branch local-ahead" || bad "feature branch local-ahead: $out"
git remote set-url origin ../missing.git
out=$(bash "$SK/start-point.sh" . feat/addendum); echo "$out" | grep -q "START $F2 fetch-failed" && ok "unreachable origin is fetch-failed" || bad "unreachable origin: $out"
git remote set-url origin ../origin.git
git switch -q main

echo "--- merge-task (update-ref, no checkout)"
git branch feature "$BASE"
WT_IMPL=$(bash "$SK/worktree.sh" create . T-impl T-impl feature)
expect 0 $? "worktree create impl"
(cd "$WT_IMPL" && echo x > x.txt && git add x.txt && git -c user.email=t@t -c user.name=t commit -qm task1)
H1=$(git -C "$WT_IMPL" rev-parse HEAD)
bash "$SK/merge-task.sh" . feature "$BASE" "$H1" >/dev/null; expect 0 $? "ff without checkout"
[ "$(git rev-parse feature)" = "$H1" ] && ok "branch moved" || bad "branch moved"
[ "$(git branch --show-current)" = "main" ] && ok "main checkout untouched" || bad "main checkout untouched"
bash "$SK/merge-task.sh" . feature "$BASE" "$H1" >/dev/null; expect 0 $? "repeat of an applied merge (resume after a crash) is a no-op"
[ "$(git rev-parse feature)" = "$H1" ] && ok "repeat leaves branch at H1" || bad "repeat leaves branch at H1"
H1b=$(git -C "$WT_IMPL" commit-tree "$H1^{tree}" -p "$BASE" -m other)
bash "$SK/merge-task.sh" . feature "$BASE" "$H1b" >/dev/null; expect 3 $? "stale expected_old refused"
git branch side "$BASE"; (cd "$WT_IMPL" && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m t2); H2=$(git -C "$WT_IMPL" rev-parse HEAD)
bash "$SK/merge-task.sh" . feature "$H1" "$(git rev-parse side)" >/dev/null; expect 3 $? "non-ff target refused"
git switch -q feature
bash "$SK/merge-task.sh" . feature "$H1" "$H2" >/dev/null; expect 2 $? "refused while checked out"
git switch -q main
bash "$SK/branch-at.sh" . feature "$H1" >/dev/null; expect 0 $? "branch-at still at H1"

echo "--- worktree.sh remove"
bash "$SK/worktree.sh" remove . "$WT_DOCS" --delete-branch >/dev/null; expect 0 $? "remove merged docs worktree"
[ ! -d "$WT_DOCS" ] && ok "docs worktree removed" || bad "docs worktree still exists"
bash "$SK/worktree.sh" remove . "$WT_IMPL" --delete-branch >/dev/null 2>&1; expect 1 $? "refuses deleting unmerged branch"
[ -d "$WT_IMPL" ] && ok "worktree preserved when branch delete refused" || bad "worktree deleted despite refused branch delete"
git update-ref refs/heads/feature "$H2"
bash "$SK/worktree.sh" remove . "$WT_IMPL" --delete-branch --merged-into feature >/dev/null; expect 0 $? "remove impl worktree with --merged-into feature"
[ ! -d "$WT_IMPL" ] && ok "impl worktree removed" || bad "impl worktree still exists"
git branch --list T-impl | grep -q T-impl && bad "T-impl branch still exists" || ok "T-impl branch deleted"
WT_KEEP=$(bash "$SK/worktree.sh" create . T-keep T-keep "$H2")
mkdir -p "$WT_KEEP/nested" && git -C "$WT_KEEP/nested" init -q
bash "$SK/discard-changes.sh" "$WT_KEEP" "$H2" >/dev/null; expect 0 $? "discard-changes removes nested git repo"
[ ! -e "$WT_KEEP/nested" ] && ok "nested git dir removed" || bad "nested git dir remained"
bash "$SK/worktree.sh" remove . "$WT_KEEP" >/dev/null; expect 0 $? "remove worktree without deleting branch"
git branch --list T-keep | grep -q T-keep && ok "branch preserved without --delete-branch" || bad "branch deleted unexpectedly"
WT_UP=$(bash "$SK/worktree.sh" create . T-up T-up "$BASE")
(cd "$WT_UP" && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m up)
git push -q origin T-up:T-up && git branch --set-upstream-to=origin/T-up T-up >/dev/null
bash "$SK/worktree.sh" remove . "$WT_UP" --delete-branch >/dev/null; expect 0 $? "remove worktree with branch merged into upstream"
[ ! -d "$WT_UP" ] && ok "upstream-merged worktree removed" || bad "upstream-merged worktree still exists"
git branch --list T-up | grep -q T-up && bad "T-up branch still exists" || ok "T-up branch deleted"
WT_DET=$(bash "$SK/worktree.sh" create . T-det T-det "$BASE")
git -C "$WT_DET" checkout -q --detach HEAD
bash "$SK/worktree.sh" remove . "$WT_DET" --delete-branch >/dev/null; expect 0 $? "remove detached worktree with --delete-branch"
[ ! -d "$WT_DET" ] && ok "detached worktree removed" || bad "detached worktree still exists"
WT_AHEAD=$(bash "$SK/worktree.sh" create . T-ahead T-ahead "$BASE")
git push -q origin T-ahead:T-ahead && git branch --set-upstream-to=origin/T-ahead T-ahead >/dev/null
(cd "$WT_AHEAD" && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m ahead)
AHEAD_SHA=$(git -C "$WT_AHEAD" rev-parse HEAD)
git reset -q --hard "$AHEAD_SHA"
bash "$SK/worktree.sh" remove . "$WT_AHEAD" --delete-branch >/dev/null 2>&1; expect 1 $? "refuses deleting branch merged to HEAD but ahead of upstream"
[ -d "$WT_AHEAD" ] && ok "worktree preserved when branch ahead of upstream" || bad "worktree deleted despite branch ahead of upstream"
git reset -q --hard "$BASE"
git push -q origin T-ahead:T-ahead
bash "$SK/worktree.sh" remove . "$WT_AHEAD" --delete-branch >/dev/null; expect 0 $? "remove worktree once upstream catches up"
[ ! -d "$WT_AHEAD" ] && ok "caught-up worktree removed" || bad "caught-up worktree still exists"

echo "--- gate"
git worktree add -q ../impl -b impl2 "$H2"
cat > ../rv.txt <<'EOF'
FEATURE_BRANCH=feature
VERIFY_1=test -f x.txt
VERIFY_2=mkdir -p out build && touch out/cache build/artifact && echo built
EOF
python3 -I "$SK/gate.py" --flight-manifest ../rv.txt --worktree ../impl --head "$H2" --log ../gate1.log >/dev/null; expect 0 $? "gate passes (falls back to VERIFY)"
[ ! -e ../impl/build ] && ok "untracked build output removed" || bad "build output left"
[ -e ../impl/out/cache ] && ok "gitignored output kept" || bad "gitignored output removed"
bash "$SK/check-tree.sh" ../impl "$H2" >/dev/null; expect 0 $? "tree clean after gate"
echo 'GATE_1=false' >> ../rv.txt
python3 -I "$SK/gate.py" --flight-manifest ../rv.txt --worktree ../impl --head "$H2" --log ../gate2.log >/dev/null; expect 1 $? "GATE_ lines take precedence and failure is reported"
grep -q "GATE_1 exit=1" ../gate2.log && ok "failure logged" || bad "failure logged"
python3 -I "$SK/gate.py" --flight-manifest ../rv.txt --worktree ../impl --head "$H2" --log ../gate3.log --prefix VERIFY >/dev/null; expect 0 $? "--prefix VERIFY ignores GATE_"
python3 -I "$SK/gate.py" --flight-manifest ../rv.txt --worktree ../impl --head "$H1" --log ../gate4.log >/dev/null; expect 2 $? "refuses when HEAD != head"

echo "--- base-drift.sh"
D=$(mktemp -d "$T/drift.XXXX")
git -C "$D" init -q -b main && git -C "$D" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$D" branch feat
bash "$SK/base-drift.sh" "$D" main feat >/dev/null; expect 0 $? "base current"
echo a > "$D/f.txt"; git -C "$D" add f.txt; git -C "$D" -c user.email=t@t -c user.name=t commit -qm "main moves"
out=$(bash "$SK/base-drift.sh" "$D" main feat); expect 1 $? "base ahead"
case "$out" in "BASE_AHEAD 1 "*local*) ok "base ahead counts 1 local commit" ;; *) bad "base ahead line: $out" ;; esac
git -C "$D" switch -q feat; echo b > "$D/f.txt"; git -C "$D" add f.txt; git -C "$D" -c user.email=t@t -c user.name=t commit -qm "feat edits"
git -C "$D" switch -q main
out=$(bash "$SK/base-drift.sh" "$D" main feat)
case "$out" in *CONFLICTS*) ok "trial merge reports conflicts" ;; *) bad "no CONFLICTS line: $out" ;; esac
[ "$(git -C "$D" rev-parse feat)" = "$(git -C "$D" rev-parse feat)" ] && [ -z "$(git -C "$D" status --porcelain)" ] && ok "drift check wrote nothing" || bad "drift check wrote nothing"

echo "--- state"
S=../state.json
python3 -I "$SK/state.py" --file $S set BASE_SHA_02=abc DIRECTOR_CONVERSATION_ID=conv_1 >/dev/null
[ "$(python3 -I "$SK/state.py" --file $S get BASE_SHA_02)" = abc ] && ok "set/get" || bad "set/get"
python3 -I "$SK/state.py" --file $S incr FIX_ROUNDS_02 >/dev/null
[ "$(python3 -I "$SK/state.py" --file $S incr FIX_ROUNDS_02)" = 2 ] && ok "incr" || bad "incr"
python3 -I "$SK/state.py" --file $S get NOPE >/dev/null; expect 1 $? "missing key exit 1"
python3 -I "$SK/state.py" --file $S unset FIX_ROUNDS_02
python3 -I "$SK/state.py" --file $S show | grep -q FIX_ROUNDS && bad "unset" || ok "unset"

echo; echo "passed=$pass failed=$fail"; rm -rf "$T"; [ $fail -eq 0 ]
