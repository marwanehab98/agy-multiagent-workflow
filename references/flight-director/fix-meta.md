# Flight Director: JOB: fix-meta

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: fix-meta

The input is `validate_meta.py` output listing problems in your task files, or in `GROUND_CREW_PAIR`: a `PROBLEM pair ...` line is about the `GROUND_CREW_PAIR` in `flight-manifest.txt`, so fix it there. A `PROBLEM <id>: REVIEW_<n> <pair> failed its test launch; see <file>` or `PROBLEM pair '<pair>': GROUND_CREW_PAIR failed its test launch; see <file>` line means that pair can't be started here: read the file, and choose another pair within "Model choices", for that `.meta` or for `GROUND_CREW_PAIR`. Fix every `PROBLEM` line. Keep your intent where you can, but within the rules in "Model choices". If you change task files, rebuild the plan file from them. If a fix changes a choice you told the user about, tell them in a `NOTIFY`. Commit, then settle `RESULT: META_FIXED`.

