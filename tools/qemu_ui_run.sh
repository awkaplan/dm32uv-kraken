#!/bin/sh
# Boot the built image under the dm32uv-re QEMU model, drive a key script, render the frames.
# Usage: tools/qemu_ui_run.sh <tag> <until-step> '<keyseq>' [extra qemu_run.py args]
# Output: out/qemu/<tag>/run (frames, logs) and out/qemu/<tag>/frames.png.
# Key steps (dm32uv-re qemu_run.py --help, "KEYSEQ GRAMMAR"):
#   lead-in -1:0:1:31:0:20   MENU -1:0:1:17:20:120   Up 2:11:1:17   Down 2:11:1:18
#   Ok 2:7:1:17   Back 2:10:1:17   '1'..'9','0','*','#' see the table there
set -eu
TAG=$1 STEPS=$2 KEYS=$3
shift 3
# Same environment and container layout as tools/build.sh (DM32UV_RE, DM32UV_RE_IMAGE; docs/building.md).
REPO=$(cd "$(dirname "$0")/.." && pwd)
WS=$(dirname "$REPO")
if [ -z "${DM32UV_RE:-}" ]; then
    if [ -d "$WS/dm32uv-re" ]; then DM32UV_RE=$WS/dm32uv-re; else DM32UV_RE=$WS/dm32/dm32uv-re; fi
fi
RE=$(cd "$DM32UV_RE" && pwd)
NAME=dm32uv-unleashed
IMAGE=${DM32UV_RE_IMAGE:-dm32uv-re-pipeline:latest}
OUT=/w/$NAME/out/qemu/$TAG
exec docker run --rm -e DM32UV_RE=/w/dm32/dm32uv-re -v "$REPO":/w/$NAME -v "$RE":/w/dm32/dm32uv-re \
    -w /w/dm32/dm32uv-re/tools/src/dm32_patch_tools "$IMAGE" -c "
set -e
rm -rf $OUT && mkdir -p $OUT
python3 compose_qemu_image.py --patched /w/$NAME/out/kraken/progmem_KRAKEN_patched.bin \
    --out $OUT/boot.bin --iram-out > $OUT/compose.log
SER1=
if [ -n '${SPINOR:-}' ]; then
    # generic factory-default data chip (VFO A 144.125 analog, B 430.125 digital) + real SRAM init
    xz -dc /w/dm32/dm32uv-re/data/firmware/patching/datachip_generic_factory_defaults.bin.xz > $OUT/datachip.bin
    python3 -c \"
d=open('/w/$NAME/out/kraken/progmem_KRAKEN_patched.bin','rb').read()
s=bytearray(0x8000); i=d[0x72000:0x763FC]; s[0x800:0x800+len(i)]=i
open('$OUT/sram.bin','wb').write(s)\"
    SER1=\"--spinor $OUT/datachip.bin\"
fi
if [ -n '${NMEA:-}' ]; then
    # fake GPS on UART1 (see tools/nmea_feed.py)
    SER1=\"\$SER1 --serial1 tcp:127.0.0.1:5556,server,nowait\"
    python3 /w/$NAME/tools/nmea_feed.py --port 5556 &
fi
GDB=
if [ -n '${POKE:-}' ]; then
    # memory pokes over the gdb stub during the run (tools/qemu_poke.py; POKE is a path inside the container, /w/<repo>/...)
    GDB=--gdb
    python3 /w/$NAME/tools/qemu_poke.py '${POKE:-}' > $OUT/poke.log 2>&1 &
fi
python3 qemu_run.py --image $OUT/boot.bin --iram $OUT/iram.bin --sram $OUT/sram.bin \
    --rundir $OUT/run --until-step $STEPS --settle-after 25 --timeout 500 --keyseq='$KEYS' \$SER1 \$GDB $*
python3 render_frames.py --rundir $OUT/run --last 8 --out $OUT/frames.png
"
