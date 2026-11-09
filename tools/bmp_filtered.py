#!/usr/bin/env python3
"""Run dm32uv-re's build_manifest_patch.py with a filtered copy of ITS OWN claims registry.

build_image.py calls this instead of build_manifest_patch.py when image.json "exclude_claims" names a claim
in dm32uv-re's data/allocations.json (which the builder always loads and has no option to replace). The
builder module reads the registry path from its global ALLOCATIONS_JSON at call time, so it is pointed at
the filtered copy and main() runs unchanged. Usage (inside the Docker image):
  bmp_filtered.py <filtered-allocations.json> <build_manifest_patch.py args...>
"""
import pathlib
import sys

sys.path.insert(0, "/w/dm32/dm32uv-re/tools/src/dm32_patch_tools")
import build_manifest_patch as bmp  # noqa: E402

bmp.ALLOCATIONS_JSON = pathlib.Path(sys.argv[1]).resolve()
sys.exit(bmp.main(sys.argv[2:]))
