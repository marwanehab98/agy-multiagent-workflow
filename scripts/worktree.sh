#!/usr/bin/env bash
# Manage git worktrees for agy-multiagent-workflow without Orca.
#
# Usage:
#   bash worktree.sh create <main_checkout> <name> <branch> <base_ref>
#   bash worktree.sh remove <main_checkout> <worktree_path> [--delete-branch] [--merged-into <ref>]
#
# `create` creates <main_checkout>/.worktrees/<name> on new branch <branch> at <base_ref> and prints its path.
# `remove` removes <worktree_path> (never --force) and with --delete-branch deletes its merged branch (-d,
# or -D after verifying reachability from --merged-into <ref>). Checks merge status before removing the worktree.
# Exit 0 ok. Exit 1 git failure. Exit 64 bad usage.
set -u
if [ $# -lt 1 ]; then
  echo "usage: worktree.sh <create|remove> ..." >&2
  exit 64
fi
op=$1; shift

case "$op" in
  create)
    if [ $# -ne 4 ]; then
      echo "usage: worktree.sh create <main_checkout> <name> <branch> <base_ref>" >&2
      exit 64
    fi
    main_checkout=$1; name=$2; branch=$3; base_ref=$4
    main_abs=$(cd "$main_checkout" 2>/dev/null && pwd) || { echo "ERROR missing checkout: $main_checkout" >&2; exit 1; }
    wt_dir="$main_abs/.worktrees"
    wt_path="$wt_dir/$name"
    mkdir -p "$wt_dir" || exit 1
    out=$(git -C "$main_abs" worktree add -b "$branch" "$wt_path" "$base_ref" 2>&1) || {
      echo "$out" >&2
      exit 1
    }
    echo "$wt_path"
    ;;
  remove)
    if [ $# -lt 2 ]; then
      echo "usage: worktree.sh remove <main_checkout> <worktree_path> [--delete-branch] [--merged-into <ref>]" >&2
      exit 64
    fi
    main_checkout=$1; wt_path=$2; shift 2
    del_branch=0; merged_into=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --delete-branch) del_branch=1; shift ;;
        --merged-into)
          [ $# -ge 2 ] || { echo "usage: worktree.sh remove <main_checkout> <worktree_path> [--delete-branch] [--merged-into <ref>]" >&2; exit 64; }
          merged_into=$2; shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 64 ;;
      esac
    done
    main_abs=$(cd "$main_checkout" 2>/dev/null && pwd) || { echo "ERROR missing checkout: $main_checkout" >&2; exit 1; }
    wt_abs=$(cd "$wt_path" 2>/dev/null && pwd) || { echo "ERROR missing worktree: $wt_path" >&2; exit 1; }
    branch=""
    if [ $del_branch -eq 1 ]; then
      branch=$(git -C "$wt_abs" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
      [ "$branch" = "HEAD" ] && branch=""
      if [ -n "$branch" ]; then
        if [ -n "$merged_into" ]; then
          target_sha=$(git -C "$main_abs" rev-parse --verify -q "$merged_into^{commit}") || {
            echo "error: unknown --merged-into ref '$merged_into'." >&2
            exit 1
          }
          if ! git -C "$main_abs" merge-base --is-ancestor "refs/heads/$branch" "$target_sha" 2>/dev/null; then
            echo "error: The branch '$branch' is not fully merged into '$merged_into'." >&2
            exit 1
          fi
        else
          merged=0
          if git -C "$main_abs" merge-base --is-ancestor "refs/heads/$branch" HEAD 2>/dev/null; then
            merged=1
          elif upstream=$(git -C "$main_abs" rev-parse --verify -q "$branch@{upstream}" 2>/dev/null); then
            git -C "$main_abs" merge-base --is-ancestor "refs/heads/$branch" "$upstream" 2>/dev/null && merged=1
          fi
          if [ $merged -eq 0 ]; then
            echo "error: The branch '$branch' is not fully merged." >&2
            exit 1
          fi
        fi
      fi
    fi
    out=$(git -C "$main_abs" worktree remove "$wt_abs" 2>&1) || {
      echo "$out" >&2
      exit 1
    }
    if [ $del_branch -eq 1 ] && [ -n "$branch" ]; then
      del_flag="-d"
      [ -n "$merged_into" ] && del_flag="-D"
      out=$(git -C "$main_abs" branch "$del_flag" "$branch" 2>&1) || {
        echo "$out" >&2
        exit 1
      }
    fi
    echo "REMOVED $wt_abs"
    ;;
  *)
    echo "usage: worktree.sh <create|remove> ..." >&2
    exit 64
    ;;
esac
