#!/bin/bash
set -u
cd "$(dirname "$0")"
# Size before the run, so a log left by an earlier run is never presented as
# the record of this one.
install_log_size() {
    if [ -f install.log ]; then
        wc -c < install.log | tr -d '[:space:]'
    else
        echo none
    fi
}
log_before="$(install_log_size)"
./setup.sh "$@"
status=$?
echo
if [ "$status" -eq 0 ]; then
    echo "Whisper Face installation finished."
else
    echo "Installation failed with exit code $status."
    if [ "$(install_log_size)" != "$log_before" ]; then
        echo "The whole run, including the error above, was recorded here:"
        echo "  $(pwd)/install.log"
        echo "Attach that file to a bug report; it holds every step that ran."
    else
        echo "Setup stopped before it could open a log, so the error above is"
        echo "all there is. Please copy these lines into a bug report."
    fi
fi
# Hold the window open on any failure, not only on a bare double-click. An
# error that scrolled off the screen is the whole reason this shim exists.
if [ "$status" -ne 0 ] || [ "$#" -eq 0 ]; then
    read -r -p "Press Return to close this window..." _
fi
exit "$status"
