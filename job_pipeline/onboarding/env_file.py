"""Read/update keys in the repo's .env without disturbing other lines."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import paths


def set_env_values(values: dict[str, str], path: Path | None = None) -> Path:
    """Upsert KEY=value lines. Comments and unrelated keys are preserved.
    The file is created with owner-only permissions (it holds secrets)."""
    p = path or paths.ENV_FILE
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def mask(secret: str) -> str:
    """`sk-ant-…ab12` style preview for showing a saved key."""
    if not secret:
        return ""
    return f"{secret[:7]}…{secret[-4:]}" if len(secret) > 12 else "••••"
