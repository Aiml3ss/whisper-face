#!/bin/bash
set -u
cd "$(dirname "$0")"
./setup.sh "$@"
status=$?
echo
if [ "$status" -eq 0 ]; then
    echo "Whisper Face installation finished."
else
    echo "Installation failed with exit code $status."
fi
if [ "$#" -eq 0 ]; then
    read -r -p "Press Return to close this window..." _
fi
exit "$status"
