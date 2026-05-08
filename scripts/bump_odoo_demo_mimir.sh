#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/bump_odoo_demo_mimir.sh [options]

Bump the sibling ../odoo-demo checkout to a specific mimir commit.
By default, this uses the current mimir HEAD commit.

Options:
  --sha <commit-ish>        Commit or ref from the current mimir repo to pin in odoo-demo
  --odoo-demo-dir <path>    Override the default sibling ../odoo-demo checkout
  --allow-unpushed          Allow a commit that is not yet reachable from origin/*
  --dry-run                 Print what would change without editing anything
  -h, --help                Show this help

This updates both:
  - odoo-demo/_vendor/mimir submodule checkout
  - odoo-demo/requirements.txt mimir git pin
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
mimir_root="$(cd "$script_dir/.." && pwd -P)"
odoo_demo_dir="$mimir_root/../odoo-demo"
target_ref="HEAD"
allow_unpushed=0
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --sha" >&2
        exit 1
      fi
      target_ref="${2:-}"
      shift 2
      ;;
    --odoo-demo-dir)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --odoo-demo-dir" >&2
        exit 1
      fi
      odoo_demo_dir="${2:-}"
      shift 2
      ;;
    --allow-unpushed)
      allow_unpushed=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$odoo_demo_dir" ]]; then
  echo "odoo-demo checkout not found: $odoo_demo_dir" >&2
  exit 1
fi

odoo_demo_dir="$(cd "$odoo_demo_dir" && pwd -P)"

if [[ ! -e "$odoo_demo_dir/.git" ]]; then
  echo "Not a git checkout: $odoo_demo_dir" >&2
  exit 1
fi

requirements_file="$odoo_demo_dir/requirements.txt"
submodule_dir="$odoo_demo_dir/_vendor/mimir"

if [[ ! -f "$requirements_file" ]]; then
  echo "requirements.txt not found: $requirements_file" >&2
  exit 1
fi

target_sha="$(git -C "$mimir_root" rev-parse "${target_ref}^{commit}")"

if [[ -n "$(git -C "$mimir_root" status --porcelain)" ]]; then
  echo "Warning: $mimir_root has uncommitted changes." >&2
  echo "Only the committed target ref will be bumped: $target_sha" >&2
fi

if [[ "$allow_unpushed" -eq 0 ]]; then
  if ! git -C "$mimir_root" branch -r --contains "$target_sha" | grep -q 'origin/'; then
    echo "Refusing to pin unpushed commit $target_sha" >&2
    echo "Push mimir first, or rerun with --allow-unpushed." >&2
    exit 1
  fi
fi

extract_requirements_sha() {
  local file_path="$1"
  local shas

  shas="$(grep -oE 'github\.com/datastructsro/mimir\.git@[0-9a-f]{40}' "$file_path" \
    | sed -E 's|.*@([0-9a-f]{40})|\1|' \
    | sort -u || true)"

  if [[ -z "$shas" ]]; then
    echo "Could not find a mimir git pin in $file_path" >&2
    exit 1
  fi

  if [[ "$(echo "$shas" | wc -l | tr -d ' ')" -ne 1 ]]; then
    echo "Expected exactly one mimir SHA in $file_path, found:" >&2
    echo "$shas" >&2
    exit 1
  fi

  echo "$shas"
}

current_requirements_sha="$(extract_requirements_sha "$requirements_file")"
current_submodule_sha=""
if [[ -e "$submodule_dir/.git" || -d "$submodule_dir/.git" ]]; then
  current_submodule_sha="$(git -C "$submodule_dir" rev-parse HEAD)"
fi

echo "mimir repo:      $mimir_root"
echo "target SHA:      $target_sha"
echo "odoo-demo repo:  $odoo_demo_dir"
if [[ -n "$current_submodule_sha" ]]; then
  echo "current submodule SHA:    $current_submodule_sha"
fi
echo "current requirements SHA: $current_requirements_sha"

if [[ "$dry_run" -eq 1 ]]; then
  echo "Dry run: would update _vendor/mimir and requirements.txt to $target_sha"
  exit 0
fi

git -C "$odoo_demo_dir" submodule update --init _vendor/mimir
git -C "$submodule_dir" fetch origin
git -C "$submodule_dir" checkout "$target_sha"

python3 - "$requirements_file" "$target_sha" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
target_sha = sys.argv[2]
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r"(^mimir\s*@\s*git\+https://github\.com/datastructsro/mimir\.git@)[0-9a-f]{40}(\s*$)",
    re.MULTILINE,
)
updated, replacements = pattern.subn(rf"\1{target_sha}\2", text)
if replacements != 1:
    raise SystemExit(
        f"Expected to replace exactly one mimir requirement line in {path}, replaced {replacements}"
    )
path.write_text(updated, encoding="utf-8")
PY

updated_submodule_sha="$(git -C "$submodule_dir" rev-parse HEAD)"
updated_requirements_sha="$(extract_requirements_sha "$requirements_file")"

if [[ "$updated_submodule_sha" != "$target_sha" ]]; then
  echo "Submodule checkout mismatch: expected $target_sha got $updated_submodule_sha" >&2
  exit 1
fi

if [[ "$updated_requirements_sha" != "$target_sha" ]]; then
  echo "requirements.txt mismatch: expected $target_sha got $updated_requirements_sha" >&2
  exit 1
fi

echo
echo "Updated odoo-demo to mimir@$target_sha"
echo
git -C "$odoo_demo_dir" status --short _vendor/mimir requirements.txt
echo
echo "Next steps:"
echo "  git -C \"$odoo_demo_dir\" add _vendor/mimir requirements.txt"
echo "  git -C \"$odoo_demo_dir\" commit -m \"chore(deps): bump mimir to ${target_sha:0:7}\""
echo "  git -C \"$odoo_demo_dir\" push"
