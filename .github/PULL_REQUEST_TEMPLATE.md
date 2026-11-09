## What this changes

<!-- One feature or fix. What and why. Link the issue if there is one. -->

## How it was tested

- [ ] `tools/build.sh` passes (all host, QEMU and update-file tests)
- [ ] New or changed behaviour has a test
- [ ] Tried on a radio (say which feature and how; never test new transmit code on the air first)

## Checklist

- [ ] The update file stays under the BFUV32-V2 size limit (0xF4002 bytes total; the build checks this)
- [ ] Nothing writes the per-unit sector 0x0300B000 or the data chip's calibration
- [ ] Nothing transmits by default; any new transmit path is off until the user turns it on
- [ ] New code and data are in erased flash claimed in `allocations.json`
- [ ] User guide page and `docs/feature-status.md` updated if behaviour, menus or status changed
- [ ] No personal data (callsigns, DMR IDs, locations, serial numbers, radio backups) in code, docs or commits
