"""fwreg.py - the stock firmware builds Kraken supports (firmware/firmwares.json) and their 1 MiB base images.

  firmwares()          the list, reference first
  get(id_or_version)   one entry
  reference()          the reference entry (every source and spec is written against it)
  base_path(fw)        where its 1 MiB base image is (the reference's comes from dm32uv-re; the others are
                       composed under out/stock/<id>/ by tools/fetch_stock.py, from update files downloaded there or
                       found in $DM32_STOCK_DIR)
  compose(ref_img, update_file_bytes)
                       a base image from an update file: the reference's boot region below 0x0300C000, the
                       update body from there, 0xFF after it
  version_of(img, fw)  the version string at fw's version_at, or None
  re_root()            the dm32uv-re checkout (DM32UV_RE, ../dm32uv-re or ../dm32/dm32uv-re)

Plain Python 3, no dependencies: tools/build.sh runs tools/fetch_stock.py on the host before Docker.
"""
import hashlib
import json
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = REPO / "firmware" / "firmwares.json"
FLASH_BASE = 0x03000000
SIZE = 0x100000
APP = 0xC000
HEADER = b"BFUV32-V2" + b"\xff" * (0xFD - 9) + b"END"
STOCK_DIR = REPO / "out" / "stock"            # composed base images (and downloaded update files)
HAND_DIR = os.environ.get("DM32_STOCK_DIR")     # optional: update files fetched by hand


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def _load():
    return json.loads(REGISTRY.read_text())


def firmwares():
    doc = _load()
    fws = doc["firmwares"]
    ref = [f for f in fws if f["id"] == doc["reference"]]
    return ref + [f for f in fws if f["id"] != doc["reference"]]


def reference():
    return firmwares()[0]


def get(key):
    for f in firmwares():
        if key in (f["id"], f["version"]):
            return f
    raise KeyError(f"firmware {key!r} is not in {REGISTRY.relative_to(REPO)} "
                   f"(known: {', '.join(f['id'] for f in firmwares())})")


def is_reference(fw):
    return fw["id"] == reference()["id"]


def re_root():
    env = os.environ.get("DM32UV_RE")
    if env:
        return pathlib.Path(env)
    for p in (REPO.parent / "dm32uv-re", REPO.parent / "dm32" / "dm32uv-re"):
        if p.is_dir():
            return p
    raise SystemExit("no dm32uv-re checkout: set DM32UV_RE")


def base_path(fw):
    if "base_image" in fw:
        rel = pathlib.PurePosixPath(fw["base_image"])
        if rel.parts[:2] == ("dm32", "dm32uv-re"):
            return re_root().joinpath(*rel.parts[2:])
        return REPO.parent / rel
    return STOCK_DIR / fw["id"] / f"progmem_stock_{fw['id']}.bin"


def update_path(fw):
    name = pathlib.PurePosixPath(fw["update_url"]).name
    if HAND_DIR and (pathlib.Path(HAND_DIR) / name).exists():
        return pathlib.Path(HAND_DIR) / name
    return STOCK_DIR / fw["id"] / name


def compose(ref_img, update):
    if len(ref_img) != SIZE:
        raise ValueError("reference image is not 1 MiB")
    if update[:0x100] != HEADER:
        raise ValueError("not a BFUV32-V2 update file (header)")
    body = update[0x100:]
    if APP + len(body) > SIZE:
        raise ValueError("update body does not fit the flash")
    img = bytearray(ref_img[:APP]) + bytearray(body)
    img += b"\xff" * (SIZE - len(img))
    return bytes(img)


def version_of(img, fw):
    at = int(fw["version_at"], 16) - FLASH_BASE
    want = fw["version"].encode()
    return fw["version"] if img[at:at + len(want)] == want and img[at + len(want)] == 0 else None


def load_base(fw):
    """The 1 MiB base image of fw, checked: its sha256 sidecar (and the pinned hash for the reference) and its
    version string. Raises SystemExit with a hint when an image is missing."""
    p = base_path(fw)
    if not p.exists():
        raise SystemExit(f"{p}: missing; run python3 tools/fetch_stock.py (tools/build.sh does)")
    img = p.read_bytes()
    if "base_sha256" in fw and sha256(img) != fw["base_sha256"]:
        raise SystemExit(f"{p}: sha256 {sha256(img)} is not the pinned {fw['base_sha256']}")
    side = p.with_name(p.name + ".sha256")
    if side.exists() and side.read_text().split()[0] != sha256(img):
        raise SystemExit(f"{p}: does not match its .sha256 sidecar")
    if version_of(img, fw) is None:
        raise SystemExit(f"{p}: no {fw['version']} string at {fw['version_at']}")
    return img
