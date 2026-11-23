# Flight Director: JOB: pr

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: pr

The input is the verification log.
- **Single-repo (`REPOS=none`):** write `DOCS_DIR/pr-title.txt` and `DOCS_DIR/pr-body.md`.
  - The title follows the project's format and includes `JIRA_KEY` when there is one.
  - The body follows the PR template: summarize the requirements and design in prose, and never link run documents, which aren't on the branch.
  Commit, then settle `RESULT: PR_READY <title file> <body file>`.
- **Multi-repo:** for each touched repo `<r>`, write `DOCS_DIR/pr-title-<r>.txt` and `DOCS_DIR/pr-body-<r>.md` (following that repo's PR template and cross-referencing the sibling repos' branches/changes), and write `DOCS_DIR/pr-manifest.txt` with one line per touched repo: `<r> <title file> <body file>`. Commit, then settle `RESULT: PR_READY <manifest file>`.

