#!/usr/bin/env python3
"""
Sync ALL manufacturers from the NetBox device-type library into this repo.

- Upstream: netbox-community/devicetype-library
- Targets:  device-types/<Vendor>/*.yml/.yaml and elevation-images/<Vendor>/*
- Safe for Nautobot 2.x: we don't rely on slugs; we keep YAML as-is.
- Unknown/odd fields are preserved; Nautobot importer ignores what it doesn't need.

Tip: run locally with `python scripts/sync_from_netbox.py`
"""
from __future__ import annotations
import pathlib, sys, hashlib, urllib.request, yaml, re

BASE = "https://raw.githubusercontent.com/netbox-community/devicetype-library/refs/heads/master"
DT_ROOT = f"{BASE}/device-types"
IMG_ROOT = f"{BASE}/elevation-images"
LOCAL_DT = pathlib.Path("device-types")
LOCAL_IMG = pathlib.Path("elevation-images")

# Optional: exclude a few vendors if you want (leave empty for full sync)
EXCLUDE_VENDORS = set()  # e.g., {"Generic", "Whitebox"}

def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()

def sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()

def list_dir(url: str) -> list[str]:
    """Scrape GitHub directory HTML for file/dir names (lightweight and robust enough here)."""
    html = fetch(url + "/").decode()
    # matches end of <a> tags that look like filenames/dirs
    names = []
    for m in re.finditer(r'">(.*?)</a>', html):
        name = m.group(1).strip()
        if name not in (".", ".."):
            names.append(name)
    return sorted(set(names))

def sync_yaml_folder(up_url: str, dst_dir: pathlib.Path, label: str) -> tuple[int,int,int]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    added = updated = skipped = 0
    for name in list_dir(up_url):
        if not name.endswith((".yml", ".yaml")): 
            continue
        src = f"{up_url}/{name}"
        data = fetch(src)

        # Parse once to catch clearly broken YAMLs (rare)
        try:
            doc = yaml.safe_load(data) or {}
        except Exception as e:
            print(f"SKIP {label}/{name}: YAML parse error: {e}", file=sys.stderr)
            skipped += 1
            continue

        # Minimal sanity for Nautobot importer
        if not isinstance(doc, dict) or "manufacturer" not in doc or "model" not in doc:
            print(f"SKIP {label}/{name}: missing manufacturer/model", file=sys.stderr)
            skipped += 1
            continue

        # No slug normalization needed for Nautobot 2.x; leave upstream as-is
        dst = dst_dir / name
        if dst.exists() and sha1(dst.read_bytes()) == sha1(data):
            continue
        updated += dst.exists()
        added += int(not dst.exists())
        dst.write_bytes(data)
    return added, updated, skipped

def sync_images_folder(up_url: str, dst_dir: pathlib.Path, label: str) -> tuple[int,int,int]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    added = updated = skipped = 0
    for name in list_dir(up_url):
        # Only files (quick heuristic); GitHub listing will include subdirs (vendors) handled outside
        if "." not in name:
            continue
        src = f"{up_url}/{name}"
        try:
            data = fetch(src)
        except Exception:
            skipped += 1
            continue
        dst = dst_dir / name
        if dst.exists() and sha1(dst.read_bytes()) == sha1(data):
            continue
        updated += dst.exists()
        added += int(not dst.exists())
        dst.write_bytes(data)
    return added, updated, skipped

def main():
    LOCAL_DT.mkdir(exist_ok=True, parents=True)
    LOCAL_IMG.mkdir(exist_ok=True, parents=True)

    vendors = [v for v in list_dir(DT_ROOT) if "." not in v]  # directories (vendors)
    if EXCLUDE_VENDORS:
        vendors = [v for v in vendors if v not in EXCLUDE_VENDORS]

    total = {"added":0,"updated":0,"skipped":0}

    # device-types/<Vendor>
    for v in vendors:
        a,u,s = sync_yaml_folder(f"{DT_ROOT}/{v}", LOCAL_DT / v, f"device-types/{v}")
        total["added"] += a; total["updated"] += u; total["skipped"] += s
        print(f"device-types/{v}: +{a} ~{u} skip {s}")

    # elevation-images/<Vendor> (optional, but nice to have for UI previews)
    img_vendors = [v for v in list_dir(IMG_ROOT) if "." not in v]
    img_vendors = [v for v in img_vendors if not EXCLUDE_VENDORS or v not in EXCLUDE_VENDORS]
    for v in img_vendors:
        a,u,s = sync_images_folder(f"{IMG_ROOT}/{v}", LOCAL_IMG / v, f"elevation-images/{v}")
        total["added"] += a; total["updated"] += u; total["skipped"] += s
        print(f"elevation-images/{v}: +{a} ~{u} skip {s}")

    print(f"TOTAL: added {total['added']}, updated {total['updated']}, skipped {total['skipped']}")

if __name__ == "__main__":
    main()
