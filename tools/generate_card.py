"""Generate a workshop mod card README.md from the mod YAML.

What this does
--------------
Reads two inputs and fills the single card template (English) that every mod
repository in the workshop carries:

  * the mod YAML (``mod``, ``info``, ``acquire``, ``based_on``) — the ``info``
    block carries the public text values verbatim: ``info.Author`` is the whole
    author list (an input document, entered manually — the authors of the source
    exhibits plus whoever assembled the version), ``info.SmallDescriptionEng``
    the short description and ``info.FullDescriptionEng`` the full one;
    ``info.nexusmods`` the Nexus Mods page URL and ``info.SectionEng`` the
    deployment subfolder under ``/Mods`` (SectionEng);
  * the ``acquire`` section is reproduced verbatim as it appears in the input
    YAML (an ordered chain of how the readable sources were obtained).

The ``based_on`` block drives the conditional ``## 🔗 Based on`` section: when
it is non-empty the generator reproduces the raw ``based_on:`` block (one
``source`` + ``note`` per entry) verbatim inside a YAML code block and includes
the section; when it is empty or absent the section is omitted entirely.

The ``## 📁 Mod files`` section is filled from the sources manifest (a
``.manifest.json``) when one is supplied; otherwise it is left empty — hashes
are never fabricated. The template's fixed bullets point at the repo's own
``mod/`` and ``src/`` folders (relative), at the Nexus Mods page
(``{{NEXUSMODS}}`` from ``info.nexusmods``, omitted when empty) and at the
latest release (``{{REPOSITORY}}/releases/latest``, rendered from the
``{{REPOSITORY}}`` placeholder); ``org`` comes from ``--org``. The deploy-path
hint ``{{DEPLOY_PATH}}`` is ``/Mods/{info.SectionEng}/{mod}``.

The layout comes from the template file ``template/mod-card.md`` (next to this
folder's sibling); the generator only substitutes the values — the template file
is the single source of the card structure. The generator does not validate the
YAML and has no per-mod config.

Usage
-----
    python generate_card.py --yaml mods/AMod_Spacejunk.yaml \
        --out ../../AMod_Spacejunk/README.md
    python generate_card.py --yaml mods/AMod_Spacejunk.yaml \
        --manifest AMod_Spacejunk.manifest.json --out ../../AMod_Spacejunk/README.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

TOOL_NAME = "generate_card.py"
TOOL_VERSION = "1.0.0"
DEFAULT_ORG = "space-rangers-mods-workshop"

TOOLS_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = TOOLS_DIR.parent / "template" / "mod-card.md"

_BLOCK_OPEN_RE = re.compile(r"\{\{#(\w+)\}\}")
_BLOCK_CLOSE_RE = re.compile(r"\{\{/(\w+)\}\}")


def strip_conditional_blocks(template: str, flags: dict[str, bool]) -> str:
    """Remove ``{{#NAME}} ... {{/NAME}}`` blocks whose flag is False.

    Keeps the block (markers stripped) when the flag is True. Handles
    non-nested blocks only — enough for the card template.
    """
    out_lines: list[str] = []
    skip_depth = 0
    skip_names: list[str] = []
    for line in template.splitlines():
        close = _BLOCK_CLOSE_RE.search(line)
        open_m = _BLOCK_OPEN_RE.search(line)
        if open_m and not close:
            name = open_m.group(1)
            if skip_depth > 0:
                skip_depth += 1
            elif not flags.get(name):
                skip_depth = 1
                skip_names.append(name)
            continue
        if close:
            name = close.group(1)
            if skip_depth > 0:
                skip_depth -= 1
                if skip_depth == 0 and name in skip_names:
                    skip_names.remove(name)
                continue
            # not skipped — drop the closing marker line entirely
            continue
        if skip_depth == 0:
            out_lines.append(line)
    return "\n".join(out_lines)


def extract_section(yaml_path: Path, key: str, fallback: list) -> str:
    """Return a top-level ``key:`` section of the mod YAML verbatim.

    The card reproduces the section 1:1 as it appears in the input YAML, so the
    generator copies the raw text of the top-level ``key:`` and its indented
    block instead of re-serializing — ``yaml.dump`` would turn empty fields into
    ``null`` and reorder the keys. Used for both ``acquire`` and ``based_on``.
    Falls back to a ``yaml.dump`` of the parsed steps if the key cannot be
    located in the raw text.
    """
    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"{key}:" and not line.startswith((" ", "\t")):
            block = [line]
            for rest in lines[i + 1:]:
                if rest == "" or rest.startswith((" ", "\t")):
                    block.append(rest)
                else:
                    break
            if len(block) > 1:
                return "\n".join(block)
            break
    # Fallback: re-serialize the parsed steps (best effort).
    if not fallback:
        return ""
    return yaml.dump(fallback, sort_keys=False).rstrip()


def render_files_table(files: list[dict]) -> str:
    """Render the files table (header + separator + rows) with aligned columns."""
    if not files:
        return ""
    width_path = max(len("file"), *(len(f["path"]) for f in files))
    width_sha = max(len("SHA-256"), *(len(f["sha256"]) for f in files))
    header = f"| {'file'.ljust(width_path)} | {'SHA-256'.ljust(width_sha)} |"
    separator = f"|{'-' * (width_path + 2)}|{'-' * (width_sha + 2)}|"
    rows = "\n".join(
        f"| {f['path'].ljust(width_path)} | {f['sha256'].ljust(width_sha)} |" for f in files
    )
    return "\n".join([header, separator, rows])


def render_card(mod: str, acquire_block: str, files_block: str, author: str,
                short_desc: str, full_desc: str, template: str, org: str,
                based_on_block: str, nexusmods: str = "", section: str = "") -> str:
    deploy_path = f"/Mods/{section}/{mod}" if section else ""
    flags = {"HAS_BASED_ON": bool(based_on_block.strip()), "HAS_NEXUS": bool(nexusmods)}
    template = strip_conditional_blocks(template, flags)

    if not acquire_block.strip():
        acquire_block = "_no acquisition steps recorded_"

    repository = f"https://github.com/{org}/{mod}"

    return (
        template
        .replace("{{MOD}}", mod)
        .replace("{{ACQUIRE}}", acquire_block)
        .replace("{{FILES}}", files_block)
        .replace("{{AUTHOR}}", author)
        .replace("{{SHORT_DESCRIPTION}}", short_desc)
        .replace("{{FULL_DESCRIPTION}}", full_desc)
        .replace("{{BASED_ON}}", based_on_block)
        .replace("{{REPOSITORY}}", repository)
        .replace("{{NEXUSMODS}}", nexusmods)
        .replace("{{DEPLOY_PATH}}", deploy_path)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yaml", required=True, help="path to the mod YAML")
    parser.add_argument("--manifest", help="path to the mod .manifest.json (optional — files section left empty without it)")
    parser.add_argument("--out", default="README.md", help="output path for the card README.md")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"workshop org (default: {DEFAULT_ORG})")
    args = parser.parse_args()

    with open(args.yaml, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mod = (data.get("mod") or "").strip()
    if not mod:
        print("ERROR: YAML is missing the 'mod' field")
        raise SystemExit(1)

    info = data.get("info") or {}
    author = (info.get("Author") or "").strip()
    short_desc = (info.get("SmallDescriptionEng") or "").strip()
    full_desc = (info.get("FullDescriptionEng") or "").strip()
    nexusmods = (info.get("nexusmods") or "").strip()
    section = (info.get("SectionEng") or "").strip()
    based_on = data.get("based_on") or []

    acquire_block = extract_section(Path(args.yaml), "acquire", data.get("acquire") or [])
    based_on_block = extract_section(Path(args.yaml), "based_on", based_on)

    files_block = ""
    if args.manifest:
        with open(args.manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
        files_block = render_files_table(manifest.get("files") or [])

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_card(mod, acquire_block, files_block, author, short_desc, full_desc,
                    template, args.org, based_on_block, nexusmods, section),
        encoding="utf-8",
    )
    print(f"card: {out}")
    print(f"  mod: {mod} · author: {author or '(empty)'} · acquire section: {'yes' if acquire_block.strip() else 'no'} · based_on: {len(based_on)}")
    print(f"  description: short={'yes' if short_desc else 'no'} · full={'yes' if full_desc else 'no'}")


if __name__ == "__main__":
    main()
