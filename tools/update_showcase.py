"""Update the workshop showcase (mods.csv + main page README) with a published mod.

What this does
--------------
Records a mod in the workshop mod list ``mods.csv`` (one row per mod) and
rebuilds the showcase main page ``README.md`` from that ``.csv``: the ``.csv``
is the single source of the catalog, the page is generated from it and never
hand-edited. Called by the ``publish_mod.py`` orchestrator as the showcase-local
step, or directly for a standalone update.

The row columns (in order) are ``mod_name``, ``mod_author``,
``mod_workshop_repo_name``, ``mod_workshop_repo_link``, ``mod_summary``.
``mod_author`` and ``mod_summary`` are read from the mod's generated card
``README.md`` (the single source of those values, in turn built from the mod
YAML's ``info`` block) rather than asked for on the command line. If the
repository name is already present in the ``.csv`` the row is not duplicated —
the tool only fills in gaps (including a missing author/summary) and then
regenerates the page, so it is safe to run repeatedly.

The page layout comes from the template file ``template/showcase-readme.md``;
the tool only substitutes the catalog rows into the ``{{ROWS}}`` placeholder.
The same rendered page is also mirrored to ``profile/README.md``, which is
what GitHub displays on the organization profile page (the root ``README.md``
only renders when visiting the repo itself). In that copy relative image
``src`` and relative file links are rewritten to absolute GitHub URLs, because
``profile/README.md`` lives one directory deeper than the root README.

Usage
-----
    python update_showcase.py --mod AMod_Spacejunk
    python update_showcase.py --mod AMod_Spacejunk --name "AMod: Spacejunk"
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

TOOL_NAME = "update_showcase.py"
TOOL_VERSION = "1.1.0"
DEFAULT_ORG = "space-rangers-mods-workshop"
DEFAULT_HEADER = [
    "mod_name",
    "mod_author",
    "mod_workshop_repo_name",
    "mod_workshop_repo_link",
    "mod_summary",
]

TOOLS_DIR = Path(__file__).resolve().parent
SHOWCASE_DIR = TOOLS_DIR.parent
WORKSHOP_DIR = SHOWCASE_DIR.parent
TEMPLATE_PATH = SHOWCASE_DIR / "template" / "showcase-readme.md"
PROFILE_README_PATH = SHOWCASE_DIR / "profile" / "README.md"

AUTHOR_RE = re.compile(r"^\*\s+\*\*Author:\*\*\s*(.*)$")


def _col(header: list[str], name: str) -> int:
    return header.index(name) if name in header else -1


def load_rows(csv_path: Path) -> tuple[list[str], list[list[str]]]:
    """Read the header and non-empty data rows from the showcase .csv."""
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None) or []
        rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    return header, rows


def save_rows(csv_path: Path, header: list[str], rows: list[list[str]]) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def read_card_mod_summary(mod: str) -> tuple[str, str]:
    """Read ``Author`` and ``Summary`` from the mod's generated card README.

    Returns ``(author, summary)``; empty strings when the card is missing or a
    field is absent. The card is the single source of these values (it is built
    from the mod YAML's ``info`` block), so the showcase inherits them from
    there instead of asking for manual input.
    """
    card_path = WORKSHOP_DIR / mod / "README.md"
    try:
        lines = card_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "", ""

    author = ""
    summary_parts: list[str] = []
    in_summary = False
    for line in lines:
        if not author:
            m = AUTHOR_RE.match(line)
            if m:
                author = m.group(1).strip()
        stripped = line.strip()
        if stripped == "### Summary":
            in_summary = True
            continue
        if in_summary:
            if stripped == "":
                continue  # blank line right after the heading
            if stripped.startswith("#") or re.fullmatch(r"([-*_])\1{2,}", stripped):
                break  # reached the next section (e.g. "## " heading or a "---" rule)
            summary_parts.append(stripped)
    return author, " ".join(summary_parts)


def build_rows_block(header: list[str], rows: list[list[str]]) -> str:
    """Render the catalog rows into the markdown table body for {{ROWS}}."""
    i_name, i_author = _col(header, "mod_name"), _col(header, "mod_author")
    i_summary = _col(header, "mod_summary")
    i_repo, i_link = _col(header, "mod_workshop_repo_name"), _col(header, "mod_workshop_repo_link")

    def cell(row: list[str], i: int) -> str:
        return row[i].strip() if 0 <= i < len(row) else ""

    return "\n".join(
        f"| {cell(row, i_name)} | {cell(row, i_author)} "
        f"| [{cell(row, i_repo)}]({cell(row, i_link)}) | {cell(row, i_summary)} |"
        for row in rows
    )


_IMG_SRC_RE = re.compile(r'src="([^"]+)"')
_MD_LINK_RE = re.compile(r'(!?\[[^\]]*\]\()([^)]+)(\))')


def _profile_url(target: str, org: str, raw: bool) -> str:
    """Return an absolute GitHub URL for a relative path referenced from profile/README.md.

    ``profile/README.md`` lives one directory deeper than the root README, so
    a relative reference that resolves in the root README breaks there. Rewrite
    it to an absolute URL — raw for images, blob for file links. Already
    absolute targets (http(s), anchors, root-absolute) are returned unchanged.
    """
    if target.startswith(("http:", "https:", "#", "/")):
        return target
    branch = "main"
    if raw:
        return f"https://raw.githubusercontent.com/{org}/.github/{branch}/{target}"
    return f"https://github.com/{org}/.github/blob/{branch}/{target}"


def render_profile_readme(readme: str, org: str) -> str:
    """Mirror the root showcase so it also renders as the organization profile page.

    GitHub shows the org profile from ``profile/README.md`` in the ``.github``
    repo. This rewrites relative image ``src`` attributes and relative file
    links in the rendered root README to absolute GitHub URLs so the profile
    copy renders the same content one directory deeper. Markdown image links
    (``![..](..)``) are left untouched.
    """
    out = _IMG_SRC_RE.sub(
        lambda m: f'src="{_profile_url(m.group(1), org, raw=True)}"', readme
    )
    return _MD_LINK_RE.sub(_link_sub(org), out)


def _link_sub(org: str):
    def _rewrite(m: re.Match[str]) -> str:
        if m.group(1).startswith("!["):
            return m.group(0)
        target = m.group(2)
        if target.startswith(("http:", "https:", "#", "/")):
            return m.group(0)
        return f"{m.group(1)}{_profile_url(target, org, raw=False)}{m.group(3)}"

    return _rewrite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mod", required=True, help="workshop repo id of the mod (same as the repo name)")
    parser.add_argument("--name", help="display mod name (default: same as --mod)")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"workshop org (default: {DEFAULT_ORG})")
    parser.add_argument("--csv", default=str(SHOWCASE_DIR / "mods.csv"), help="path to the workshop mod list .csv")
    parser.add_argument("--readme", default=str(SHOWCASE_DIR / "README.md"), help="path to the showcase main page README.md")
    parser.add_argument("--profile-readme", default=str(PROFILE_README_PATH), help="path to the org profile README.md (default: <showcase>/profile/README.md)")
    args = parser.parse_args()

    mod = args.mod.strip()
    if not mod:
        print("ERROR: --mod must not be empty")
        raise SystemExit(1)
    name = (args.name or mod).strip() or mod
    link = f"https://github.com/{args.org}/{mod}"

    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header, rows = load_rows(csv_path)
    if not header:
        header = DEFAULT_HEADER

    i_author, i_summary = _col(header, "mod_author"), _col(header, "mod_summary")
    i_repo = _col(header, "mod_workshop_repo_name")
    i_link = _col(header, "mod_workshop_repo_link")

    # Normalize rows to the header length so later indexing is always safe.
    for row in rows:
        if len(row) < len(header):
            row.extend([""] * (len(header) - len(row)))

    # Backfill missing author/summary for existing rows from their cards.
    for row in rows:
        mod_id = row[i_repo].strip()
        if mod_id and (not row[i_author].strip() or not row[i_summary].strip()):
            author, summary = read_card_mod_summary(mod_id)
            if not row[i_author].strip():
                row[i_author] = author
            if not row[i_summary].strip():
                row[i_summary] = summary

    # Append a new row if the mod is not yet catalogued.
    if not any(row[i_repo].strip() == mod for row in rows):
        author, summary = read_card_mod_summary(mod)
        row = [""] * len(header)
        row[_col(header, "mod_name")] = name
        row[i_author] = author
        row[i_summary] = summary
        row[i_repo] = mod
        row[i_link] = link
        rows.append(row)
        print(f"showcase: adding row {name!r} ({mod})")

    save_rows(csv_path, header, rows)

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    readme = template.replace("{{ROWS}}", build_rows_block(header, rows))
    readme_path = Path(args.readme)
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(readme, encoding="utf-8")

    profile_path = Path(args.profile_readme)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(render_profile_readme(readme, args.org), encoding="utf-8")

    print(f"showcase: {csv_path} ({len(rows)} mod(s))")
    print(f"showcase: {readme_path}")
    print(f"showcase: {profile_path}")


if __name__ == "__main__":
    main()
