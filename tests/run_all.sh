#!/bin/bash
# The single test entry point: shell tests, then the Python tests. Fails if either fails.
cd "$(dirname "$0")/.." || exit 1
rc=0
bash tests/test_scripts.sh || rc=1
python3 -I -m unittest discover -s tests -p 'test_*.py' || rc=1
exit $rc
