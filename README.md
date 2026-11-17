# orca-multiagent-workflow

A Claude Code skill that takes a JIRA story or written requirements to a reviewed PR through Orca workers. Start with `SKILL.md`.

## Release

Changes here don't reach running sessions until you install them:

```sh
bash install.sh                 # test HEAD in a clean copy, then install it
bash install.sh --ref <commit>  # install an older version (rollback)
```

It installs into `~/.claude/skills/orca-multiagent-workflow`. The replaced version is kept in `~/.local/share/orca-multiagent-workflow/backups` (newest 5). `VERSION` in the installed folder names the commit.

## Tests

```sh
bash tests/run_all.sh
```
