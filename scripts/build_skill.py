#!/usr/bin/env python3
"""Package skills/llm-council/ into a ready-to-upload llm-council.zip.

The zip contains a single top-level folder `llm-council/` with SKILL.md inside,
which is the layout claude.ai expects (Settings > Capabilities > Skills > Upload)
and which can also be unzipped straight into ~/.claude/skills/.

Usage:
    python3 scripts/build_skill.py [--out dist/llm-council.zip]
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "llm-council"
SKILL_DIR = REPO_ROOT / "skills" / SKILL_NAME
DEFAULT_OUT = REPO_ROOT / "dist" / f"{SKILL_NAME}.zip"

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "tests", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip"}
EXCLUDE_NAMES = {".DS_Store", ".env"}
# Rough shapes of real secrets that must never end up in the package.
SECRET_PATTERNS = [re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"), re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")]


def validate_skill(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SystemExit(f"error: {skill_md} not found")
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise SystemExit("error: SKILL.md must start with YAML frontmatter delimited by ---")
    front = dict(
        (k.strip(), v.strip())
        for k, v in (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    )
    if front.get("name") != SKILL_NAME:
        raise SystemExit(f"error: frontmatter name must be '{SKILL_NAME}', got {front.get('name')!r}")
    description = front.get("description", "")
    if not description:
        raise SystemExit("error: frontmatter description is required")
    if len(description) > 1024:
        raise SystemExit(f"error: description is {len(description)} chars (max 1024)")
    if "<" in description or ">" in description:
        raise SystemExit("error: description must not contain angle brackets")


def collect_files(skill_dir: Path):
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES or path.name in EXCLUDE_NAMES:
            continue
        yield path, rel


def build(out: Path = DEFAULT_OUT, skill_dir: Path = SKILL_DIR) -> Path:
    validate_skill(skill_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    files = list(collect_files(skill_dir))
    for path, rel in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                raise SystemExit(f"error: {rel} looks like it contains an API key; refusing to package")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, rel in files:
            arcname = f"{SKILL_NAME}/{rel.as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=(2025, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix == ".py" else 0o644) << 16
            zf.writestr(info, path.read_bytes())
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output zip path")
    args = parser.parse_args(argv)
    out = build(args.out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    print(f"Built {out} ({out.stat().st_size} bytes)")
    for name in names:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
