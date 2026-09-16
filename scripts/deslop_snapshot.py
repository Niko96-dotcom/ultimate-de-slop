"""Content manifest for per-attempt ownership and proof binding."""
import hashlib
import os
import stat
import subprocess
from pathlib import Path


def snapshot(root: Path) -> dict[str, str]:
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, check=True, stdout=subprocess.PIPE)
    manifest = {}
    for raw in set(result.stdout.split(b"\0")) - {b""}:
        name = os.fsdecode(raw)
        if name == ".deslop" or name.startswith(".deslop/"):
            continue
        path = root / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            data = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(mode):
            data = path.read_bytes()
        elif stat.S_ISDIR(mode):
            # A submodule must remain visible to the ownership check.
            data = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"])
            data += subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"])
        else:
            raise ValueError(f"unsupported worktree file: {name}")
        manifest[name] = f"{stat.S_IMODE(mode)}:{hashlib.sha256(data).hexdigest()}"
    return manifest
