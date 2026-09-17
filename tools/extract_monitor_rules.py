"""Read-only extractor of historical tag/path candidates from Grabber monitors.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def extract(path: Path) -> list[dict[str, object]]:
    data=json.loads(path.read_text(encoding="utf-8-sig")); rows=[]
    for monitor in data.get("monitors", []):
        query=monitor.get("query", {}); tags=query.get("tags", []) if isinstance(query, dict) else []
        filename=str(monitor.get("filenameOverride", ""))
        if not filename or not tags: continue
        destination=filename.rsplit("/", 1)[0]
        rows.append({"tags":tags,"sites":monitor.get("sites", []),"historical_path":destination,
                     "postFilters":monitor.get("postFilters", [])})
    return rows

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("monitors", type=Path); parser.add_argument("output", type=Path)
    args=parser.parse_args(); args.output.write_text(json.dumps(extract(args.monitors),ensure_ascii=False,indent=2),encoding="utf-8"); return 0
if __name__ == "__main__": raise SystemExit(main())
