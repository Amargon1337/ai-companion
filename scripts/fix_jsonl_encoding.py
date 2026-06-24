"""Fix mojibake (UTF-8/CP1251 corruption) in .jsonl files under data/."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def decode_mojibake(text: str) -> str:
    """Attempt to repair common UTF-8/CP1251 mojibake patterns."""
    if not text:
        return text
    for src_enc, dst_enc in [
        ("cp1251", "utf-8"),
        ("utf-8", "cp1251"),
        ("latin-1", "utf-8"),
        ("utf-8", "latin-1"),
    ]:
        try:
            re_decoded = text.encode(src_enc, errors="replace").decode(dst_enc, errors="replace")
            if re_decoded != text and any(ord(c) > 127 for c in re_decoded):
                return re_decoded
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def fix_file(path: Path) -> bool:
    original = path.read_bytes()
    try:
        decoded = original.decode("utf-8")
    except UnicodeDecodeError:
        decoded = original.decode("utf-8", errors="replace")

    fixed_lines: list[str] = []
    changed = False
    for line in decoded.splitlines(keepends=True):
        if not line.strip():
            fixed_lines.append(line)
            continue
        try:
            repaired = decode_mojibake(line)
            if repaired != line:
                changed = True
                fixed_lines.append(repaired)
            else:
                fixed_lines.append(line)
        except Exception:
            fixed_lines.append(line)

    if changed:
        result = "".join(fixed_lines)
        path.write_bytes(result.encode("utf-8"))
        return True
    return False


def main() -> int:
    data_dir = Path(__file__).resolve().parents[1] / "data"
    if not data_dir.is_dir():
        print(f"Data directory not found: {data_dir}", file=sys.stderr)
        return 1

    found = fixed = 0
    for p in sorted(data_dir.glob("*.jsonl")):
        found += 1
        if fix_file(p):
            print(f"FIXED  {p.name}")
            fixed += 1
        else:
            print(f"OK     {p.name}")

    print(f"\nScanned {found} files, fixed {fixed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
