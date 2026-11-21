#!/usr/bin/env bash
# Manage git worktrees for agy-multiagent-workflow without Orca.
#
# Usage:
#   bash worktree.sh create <main_checkout> <name> <branch> <base_ref>
#   bash worktree.sh remove <main_checkout> <worktree_path> [--delete-branch]
#
# `create` creates <main_checkout>/.worktrees/<name> on new branch <branch> at <base_ref> and prints its path.
# `remove` removes <worktree_path> (never --force) and with --delete-branch deletes its merged branch (-d).
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
    if [ $# -lt 2 ] || [ $# -gt 3 ]; then
      echo "usage: worktree.sh remove <main_checkout> <worktree_path> [--delete-branch]" >&2
      exit 64
    fi
    main_checkout=$1; wt_path=$2; del_branch=0
    if [ $# -eq 3 ]; then
      [ "$3" = "--delete-branch" ] || { echo "unknown flag: $3" >&2; exit 64; }
      del_branch=1
    fi
    main_abs=$(cd "$main_checkout" 2>/dev/null && pwd) || { echo "ERROR missing checkout: $main_checkout" >&2; exit 1; }
    wt_abs=$(cd "$wt_path" 2>/dev/null && pwd) || { echo "ERROR missing worktree: $wt_path" >&2; exit 1; }
    branch=""
    if [ $del_branch -eq 1 ]; then
      branch=$(git -C "$wt_abs" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
      [ "$branch" = "HEAD" ] && branch=""
    fi
    out=$(git -C "$main_abs" worktree remove "$wt_abs" 2>&1) || {
      echo "$out" >&2
      exit 1
    }
    if [ $del_branch -eq 1 ] && [ -n "$branch" ]; then
      out=$(git -C "$main_abs" branch -d "$branch" 2>&1) || {
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
