"""Stable project scope suggestions without changing existing scope IDs."""

import hashlib
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


def normalize_origin(value: str) -> str | None:
    remote = value.strip()
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", remote)
    if scp and "://" not in remote:
        host, path = scp.groups()
    elif "://" in remote:
        parsed = urlsplit(remote)
        host, path = parsed.hostname, parsed.path
        try:
            if host and parsed.port:
                host = f"{host}:{parsed.port}"
        except ValueError:
            return None
    else:
        return None
    if not host or not path:
        return None
    path = unquote(path).strip("/")
    path = path.removesuffix(".git")
    if not path or "/../" in f"/{path}/" or "/./" in f"/{path}/":
        return None
    host = host.lower()
    if host in {"github.com", "www.github.com"}:
        host, path = "github.com", path.lower()
    return f"{host}/{path}"


def _git(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def project_identity(project_path: str) -> dict:
    path = Path(project_path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("project_path must name an existing directory")
    root_text = _git(path, "rev-parse", "--show-toplevel")
    root = Path(root_text).resolve() if root_text else path
    origin = normalize_origin(_git(root, "remote", "get-url", "origin") or "")
    source = "git_origin" if origin else "local_path"
    key = origin if origin else (root.as_posix().casefold() if os.name == "nt" else root.as_posix())
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return {
        "scope": "project",
        "scope_id": f"project:{source}:{digest}",
        "identity_source": source,
        "git_root": str(root),
        "origin_normalized": origin,
        "notice": "A suggestion only: existing user-chosen scope IDs are not migrated automatically.",
    }
