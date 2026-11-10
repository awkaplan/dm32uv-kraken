#!/bin/sh
# Build (and by default QEMU-test) the Kraken image inside the dm32uv-re Docker image.
# Usage: tools/build.sh [--fw ID ...] [--no-test]   every firmware build in firmware/firmwares.json, or only --fw ones
#        tools/build.sh --run 'COMMAND'   run COMMAND in the same container, from this repo's root
#                                         (e.g. the extra QEMU checks: --run 'python3 tests/settings_e2e.py')
#        tools/build.sh --shell           an interactive shell there
# See docs/building.md. Environment:
#   DM32UV_RE        path of the dm32uv-re checkout (default: ../dm32uv-re next to this repo,
#                    or ../dm32/dm32uv-re if only that exists)
#   DM32UV_RE_IMAGE  Docker image (default dm32uv-re-pipeline:latest, what `docker compose build`
#                    in dm32uv-re/docker tags)
#   DM32_STOCK_DIR   a folder holding stock update files fetched by hand (optional). This script runs
#                    tools/fetch_stock.py on the host first: it takes the update files from there or downloads
#                    them, checks them and composes the base images under out/stock/
#   DM32_OFFICIAL_SAMPLE  optional: an official Baofeng BFUV32-V2 update file; the build then checks
#                    that the update file it writes has the same 0x100-byte header
# Inside the container the paths are fixed, whatever the host folders are called:
#   this repo -> /w/dm32uv-unleashed, dm32uv-re -> /w/dm32/dm32uv-re (image.json and the tests use these).
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
WS=$(dirname "$REPO")
if [ -z "${DM32UV_RE:-}" ]; then
    if [ -d "$WS/dm32uv-re" ]; then DM32UV_RE=$WS/dm32uv-re
    elif [ -d "$WS/dm32/dm32uv-re" ]; then DM32UV_RE=$WS/dm32/dm32uv-re
    else
        echo "tools/build.sh: no dm32uv-re checkout at $WS/dm32uv-re; clone https://github.com/awkaplan/dm32uv-re there or set DM32UV_RE" >&2
        exit 1
    fi
fi
RE=$(cd "$DM32UV_RE" && pwd)
[ -f "$RE/tools/src/dm32_patch_tools/build_manifest_patch.py" ] || {
    echo "tools/build.sh: $RE does not look like a dm32uv-re checkout" >&2; exit 1; }
IMAGE=${DM32UV_RE_IMAGE:-dm32uv-re-pipeline:latest}
SAMPLE_MOUNT=
SAMPLE_ENV=
if [ -n "${DM32_OFFICIAL_SAMPLE:-}" ]; then
    S=$(cd "$(dirname "$DM32_OFFICIAL_SAMPLE")" && pwd)/$(basename "$DM32_OFFICIAL_SAMPLE")
    SAMPLE_MOUNT="-v $S:/w/official_sample.bin:ro"
    SAMPLE_ENV="-e DM32_OFFICIAL_SAMPLE=/w/official_sample.bin"
fi
# the stock images of the other firmware builds (firmware/firmwares.json): fetched and checked here, on the host
if [ "${1:-}" != "--shell" ]; then
    DM32UV_RE="$RE" python3 "$REPO/tools/fetch_stock.py" || echo "tools/build.sh: fetch_stock failed; builds that need a missing stock image will stop" >&2
fi
WD=/w/dm32/dm32uv-re
TTY=
case "${1:-}" in
    --run) shift; WD=/w/dm32uv-unleashed; CMD="$*" ;;
    --shell) WD=/w/dm32uv-unleashed; TTY=-it; CMD="exec bash" ;;
    *) CMD="python3 -u /w/dm32uv-unleashed/tools/build_image.py $*" ;;
esac
# shellcheck disable=SC2086
exec docker run --rm $TTY -e DM32_TIMEOUT_SCALE -e OPTROW_PARALLEL -e DM32_TIMINGS -e DM32_IDLE_SPIN \
    -e DM32UV_RE=/w/dm32/dm32uv-re $SAMPLE_ENV \
    -v "$REPO":/w/dm32uv-unleashed -v "$RE":/w/dm32/dm32uv-re $SAMPLE_MOUNT \
    -w "$WD" "$IMAGE" -c "$CMD"
