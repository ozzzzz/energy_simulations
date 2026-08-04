#!/usr/bin/env python3
"""Rename the template from 'app' to your project name.

Usage:
    python rename.py my_service
    python rename.py my_service --env-prefix MY
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent

TEXT_EXTENSIONS = {".py", ".toml", ".json", ".yml", ".yaml", ".md", ".txt", ".cfg", ".ini", ".env"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def replace_in_file(path: Path, old: str, new: str) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return False
    updated = original.replace(old, new)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def replace_in_tree(root: Path, old: str, new: str) -> list[Path]:
    changed = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in TEXT_EXTENSIONS:
            if any(skip in path.parts for skip in SKIP_DIRS):
                continue
            if replace_in_file(path, old, new):
                changed.append(path.relative_to(root))
    return changed


def validate_name(name: str) -> None:
    import re

    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        print(f"Error: '{name}' is not a valid Python package name.")
        print("Use lowercase letters, digits, and underscores. Must start with a letter.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename template project")
    parser.add_argument("name", help="New project name (e.g. my_service)")
    parser.add_argument(
        "--env-prefix",
        help="New env var prefix, uppercase (default: NAME uppercased). Pass 'keep' to keep APP_",
        default=None,
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without modifying files")
    args = parser.parse_args()

    new_name = args.name.lower().replace("-", "_")
    validate_name(new_name)

    old_name = "app"
    if new_name == old_name:
        print("Nothing to do — name is already 'app'.")
        sys.exit(0)

    old_prefix = "APP_"
    if args.env_prefix == "keep":
        new_prefix = old_prefix
    elif args.env_prefix:
        new_prefix = args.env_prefix.upper().rstrip("_") + "_"
    else:
        new_prefix = new_name.upper() + "_"

    rename_prefix = new_prefix != old_prefix

    print(f"Renaming: '{old_name}' → '{new_name}'")
    if rename_prefix:
        print(f"Env prefix: '{old_prefix}' → '{new_prefix}'")
    print()

    # --- Rename source directory ---
    src_dir = ROOT / old_name
    dst_dir = ROOT / new_name
    if not src_dir.exists():
        print(f"Error: source directory '{old_name}/' not found.")
        sys.exit(1)
    if dst_dir.exists():
        print(f"Error: target directory '{new_name}/' already exists.")
        sys.exit(1)

    if args.dry_run:
        print(f"[dry-run] Would rename {src_dir} → {dst_dir}")
    else:
        shutil.copytree(src_dir, dst_dir)
        shutil.rmtree(src_dir)
        print(f"Renamed {old_name}/ → {new_name}/")

    # --- Replace text in all files ---
    replacements = [
        # Python imports and module references
        (f"from {old_name}.", f"from {new_name}."),
        (f"import {old_name}.", f"import {new_name}."),
        (f'"{old_name}.', f'"{new_name}.'),
        # pyproject.toml
        (f'name = "{old_name}"', f'name = "{new_name}"'),
        (f'{old_name} = "{old_name}.cli:cli"', f'{new_name} = "{new_name}.cli:cli"'),
        (f'packages = ["{old_name}"]', f'packages = ["{new_name}"]'),
        (f'source = ["{old_name}"]', f'source = ["{new_name}"]'),
        # pyrightconfig.json
        (f'"{old_name}"]', f'"{new_name}"]'),
    ]

    if rename_prefix:
        replacements.append((old_prefix, new_prefix))

    if args.dry_run:
        for old, new in replacements:
            hits = replace_in_tree(ROOT, old, new) if False else []  # skip actual changes
            print(f"[dry-run] Would replace: {old!r} → {new!r}")
        print("\nRun without --dry-run to apply changes.")
        return

    all_changed: set[Path] = set()
    for old, new in replacements:
        changed = replace_in_tree(ROOT, old, new)
        all_changed.update(changed)

    if all_changed:
        print("Updated files:")
        for f in sorted(all_changed):
            print(f"  {f}")

    print(f"\nDone. Your project is now named '{new_name}'.")
    if rename_prefix:
        print(f"Update your .env file: rename {old_prefix} variables to {new_prefix}")
    print("\nNext steps:")
    print("  1. cp .env.example .env  (and fill in values)")
    print("  2. uv sync")
    print(f"  3. uv run {new_name} serve")


if __name__ == "__main__":
    main()
