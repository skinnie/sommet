#!/usr/bin/env bash
#
# worktree.sh - give each parallel session its OWN isolated copy of the repo.
#
# Why: several Claude sessions / terminals were all editing ONE shared checkout on ONE
# branch, so their commits piled onto each other and looked "lost" or "out of sync" (they
# weren't - just unmerged). A git *worktree* is a second folder backed by the same repo but
# on its own branch: sessions can't stomp on each other, and each merges to main cleanly.
#
# The big build folders (node_modules 1.5G, .venv, map tiles, assets/) are gitignored, so a
# fresh worktree would be empty of them and not run. This script SYMLINKS them from the main
# checkout, so a new worktree is runnable in seconds with no 1.5G duplicate and no reinstall.
#
# Usage:
#   scripts/worktree.sh new  <name> [base]   # new worktree+branch off origin/<base> (base defaults to main)
#   scripts/worktree.sh list                 # show all worktrees
#   scripts/worktree.sh rm   <name>          # remove a worktree (its branch is kept)
#
# Worktrees are created as siblings of the main checkout, under ../ambit-wt/<name>.
set -euo pipefail

# The main worktree is always the first line of `git worktree list`.
PRIMARY="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
WT_ROOT="$(dirname "$PRIMARY")/ambit-wt"

# Gitignored dirs a fresh checkout lacks but needs to actually build/run. Symlinked, not
# copied, from the main checkout. Add to this list if a new runtime dir appears.
LINK_PATHS=(
  ".venv"
  "android/node_modules"
  "android/android/app/src/main/assets/leaflet"
  "desktop/assets/map"
  "assets"
)

link_runtime_dirs() {
  local dest="$1"
  for p in "${LINK_PATHS[@]}"; do
    if [ -e "$PRIMARY/$p" ] && [ ! -e "$dest/$p" ]; then
      mkdir -p "$dest/$(dirname "$p")"
      ln -s "$PRIMARY/$p" "$dest/$p"
      echo "  linked $p"
    fi
  done
}

cmd_new() {
  local name="${1:?usage: worktree.sh new <name> [base]}"
  local base="${2:-main}"
  local dest="$WT_ROOT/$name"
  [ -e "$dest" ] && { echo "already exists: $dest"; exit 1; }
  git fetch origin --quiet
  mkdir -p "$WT_ROOT"
  # New branch <name> off the freshest origin/<base>; if <name> already exists, check it out.
  if git show-ref --verify --quiet "refs/heads/$name"; then
    git worktree add "$dest" "$name"
  else
    git worktree add "$dest" -b "$name" "origin/$base"
  fi
  echo "Linking gitignored runtime dirs (symlinks, shared with the main checkout):"
  link_runtime_dirs "$dest"
  echo
  echo "Ready. Work in it with:"
  echo "  cd $dest"
  echo "When done, merge to main:  git push origin HEAD  (then open/merge as usual)"
}

cmd_rm() {
  local name="${1:?usage: worktree.sh rm <name>}"
  local dest="$WT_ROOT/$name"
  # Drop our symlinks first so git doesn't count them as untracked and refuse.
  for p in "${LINK_PATHS[@]}"; do [ -L "$dest/$p" ] && rm -f "$dest/$p"; done
  git worktree remove "$dest"
  echo "removed $dest (branch '$name' kept; delete it with: git branch -D $name)"
}

case "${1:-}" in
  new)  shift; cmd_new "$@";;
  list) git worktree list;;
  rm)   shift; cmd_rm "$@";;
  *)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 1;;
esac
