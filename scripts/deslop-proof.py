#!/usr/bin/env python3
"""Bind a completed stage artifact to the current fix attempt and content."""
import json
import sys
from pathlib import Path
from deslop_snapshot import snapshot

root, finding_id, artifact = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
if not artifact.is_file():
    raise SystemExit(0)
finding = next(item for item in map(json.loads, (root / '.deslop/findings.jsonl').read_text().splitlines())
               if item.get('id') == finding_id)
data = json.loads(artifact.read_text())
data['fix_run'] = (finding.get('last_fix') or {}).get('run_dir')
data['worktree_manifest'] = snapshot(root)
artifact.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
