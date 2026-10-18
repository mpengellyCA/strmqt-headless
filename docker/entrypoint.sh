#!/bin/bash
# The container's first process. A desktop session would give the streaming
# host a D-Bus session bus and a PipeWire, whose screencast KWin offers
# Sunshine: they are started here, and then the host itself, which StrmQt
# runs in the foreground (`strmqt admin host run`, StrmQt's HostSupervisor).
#
# When any of them ends the container ends, and Docker's restart policy
# starts it again. A stop is passed on to the host first, so its services
# end in order.
set -euo pipefail

runtime=${XDG_RUNTIME_DIR:?}
mkdir -p "$runtime"
chmod 0700 "$runtime"
# A container started again keeps its files: sockets left by the last run
# would be in the way.
find "$runtime" -mindepth 1 -delete

children=()
stop() {
    trap - TERM INT
    if [ -n "${host:-}" ]; then
        kill -TERM "$host" 2>/dev/null || true
        wait "$host" 2>/dev/null || true
    fi
    kill -TERM "${children[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap 'stop; exit 0' TERM INT

wait_for() {
    for _ in $(seq 1 100); do
        [ -S "$1" ] && return 0
        sleep 0.1
    done
    echo "strmqt-headless: $1 did not appear" >&2
    return 1
}

dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile &
children+=($!)
wait_for "$runtime/bus"

pipewire &
children+=($!)
wait_for "$runtime/pipewire-0"
wireplumber &
children+=($!)

strmqt admin host run &
host=$!

# Whichever ends first ends the container.
set +e
wait -n
code=$?
echo "strmqt-headless: a service ended ($code); stopping" >&2
stop
exit "${code:-1}"
