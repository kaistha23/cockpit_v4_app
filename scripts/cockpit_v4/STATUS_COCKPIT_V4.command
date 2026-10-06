#!/bin/bash
# Double-click me on macOS. Runs only Cockpit V4.
cd "$(dirname "$0")/../.." || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3 was not found on this Mac."
  read -r -p "Press return to close. "
  exit 1
fi
"$PY" scripts/cockpit_v4/status.py "$@"
status=$?
echo
read -r -p "Press return to close this window. "
exit $status
