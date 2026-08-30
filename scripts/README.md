# Parallel work without stepping on each other (worktrees)

If several sessions/terminals edit **one shared checkout on one branch**, their commits pile
onto each other and look "lost" or "out of sync" — they aren't, just unmerged, but it's
confusing and error-prone. The fix is one **git worktree per session**: a separate folder,
same repo, its own branch. Isolated work, clean merges to `main`.

## Start a new isolated workspace

```bash
scripts/worktree.sh new my-task        # folder ../ambit-wt/my-task, branch my-task off main
cd ../ambit-wt/my-task                  # work here
```

The heavy gitignored folders (`node_modules` ~1.5G, `.venv`, map tiles, `assets/`) are
**symlinked** from the main checkout, so the new worktree runs immediately — no reinstall, no
duplication.

## Everyday flow

```bash
# ...edit, then:
git add -A && git commit -m "what changed"
git push origin HEAD                    # pushes your branch
```

Merge the branch into `main` on GitHub (or ask Claude to). Because each session is on its own
branch off the latest `main`, merges stay clean.

## Manage worktrees

```bash
scripts/worktree.sh list               # see all worktrees + their branches
scripts/worktree.sh rm my-task         # remove the folder (keeps the branch)
```

## Rules of thumb

- One session = one worktree = one branch. Don't share.
- Branch off fresh `main` (`worktree.sh new` fetches first).
- The **main checkout** stays the canonical clone — the symlinks point at its big folders, so
  don't delete or move it.
- Anything not gitignored is fully independent per worktree; the symlinked folders are shared,
  so `npm install` / `pip install` in one affects all (fine for the same deps).
