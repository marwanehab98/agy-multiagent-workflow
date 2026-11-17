# orca-multiagent-workflow

A Claude Code skill that takes a JIRA story or written requirements to a reviewed PR through Orca workers. Start with `SKILL.md`.

## Release

Changes here don't reach running sessions until you install them. `install.sh` tests the commit in a clean copy first, and never installs uncommitted work.

```sh
bash install.sh                          # update every harness that already has the skill
bash install.sh --harness antigravity    # also install into another harness (repeatable)
bash install.sh --ref <commit>           # install an older version (rollback)
python3 scripts/harnesses.py install-dirs   # the harnesses on this machine and their skills folders
```

Each install has a `VERSION` file naming its commit. Replaced versions are kept in `~/.local/share/orca-multiagent-workflow/backups/<harness>` (newest 5 each).

## Tests

```sh
bash tests/run_all.sh
```
