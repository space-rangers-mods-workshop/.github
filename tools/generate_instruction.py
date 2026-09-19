"""Generate a mod's user-facing doc/instruction.md from the mod YAML.

What this does
--------------
Fills the single instruction template (``template/instruction.md``) with the
values the YAML already carries and writes the result to the mod's ``doc/``
folder:

  * ``{{MOD}}``        — ``mod``;
  * ``{{SUMMARY}}``    — ``info.SmallDescriptionEng`` (the card's short description);
  * ``{{DESCRIPTION}}``— ``info.FullDescriptionEng``;
  * ``{{REPOSITORY}}`` — ``https://github.com/<org>/<mod>`` (``org`` from ``--org``);
  * ``{{NEXUS}}``      — ``info.nexusmods`` (the ``{{#HAS_NEXUS}}`` block is dropped
                         when it is empty);
  * ``{{SECTION}}``    — ``info.SectionEng`` (the deploy folder under ``Mods\\``).

The mod-specific prose (features, how-to, shout-outs) is not in the YAML, so the
generator leaves those template sections for the author to fill in by hand. The
output is only written when missing; pass ``--force`` to overwrite a
hand-edited instruction.

Usage
-----
    python generate_instruction.py ../../DenSettingsControl/DenSettingsControl.yaml
    python generate_instruction.py ../../DenSettingsControl/DenSettingsControl.yaml --force
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from generate_card import strip_conditional_blocks

TOOL_NAME = "generate_instruction.py"
TOOL_VERSION = "1.0.0"
DEFAULT_ORG = "space-rangers-mods-workshop"

TOOLS_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = TOOLS_DIR.parent / "template" / "instruction.md"

_COLOR_TAG_RE = re.compile(r"</?color(?:=[^>]*)?>", re.IGNORECASE)


def strip_color_tags(text: str) -> str:
    """Drop the game's inline ``<color=...>`` markup — the instruction is plain Markdown."""
    return _COLOR_TAG_RE.sub("", text).strip()


def render(mod: str, info: dict, template: str, org: str) -> str:
    repository = f"https://github.com/{org}/{mod}"
    nexus = (info.get("nexusmods") or "").strip()
    section = (info.get("SectionEng") or "").strip()
    template = strip_conditional_blocks(template, {"HAS_NEXUS": bool(nexus)})
    return (
        template
        .replace("{{MOD}}", mod)
        .replace("{{SUMMARY}}", strip_color_tags(info.get("SmallDescriptionEng") or ""))
        .replace("{{DESCRIPTION}}", strip_color_tags(info.get("FullDescriptionEng") or ""))
        .replace("{{REPOSITORY}}", repository)
        .replace("{{NEXUS}}", nexus)
        .replace("{{SECTION}}", section)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("yaml_path", help="path to the mod YAML (<mod>/<mod>.yaml)")
    parser.add_argument("--out", help="output path (default: <yaml folder>/doc/instruction.md)")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"workshop org (default: {DEFAULT_ORG})")
    parser.add_argument("--force", action="store_true", help="overwrite an existing instruction (hand-edited by default)")
    args = parser.parse_args()

    with open(args.yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mod = (data.get("mod") or "").strip()
    if not mod:
        print("ERROR: YAML is missing the 'mod' field")
        raise SystemExit(1)

    out = Path(args.out) if args.out else Path(args.yaml_path).resolve().parent / "doc" / "instruction.md"
    if out.exists() and not args.force:
        print(f"instruction: {out} already present — kept as-is (use --force to overwrite)")
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(mod, data.get("info") or {}, TEMPLATE_PATH.read_text(encoding="utf-8"), args.org), encoding="utf-8")
    print(f"instruction: {out}")
    print(f"  mod: {mod} · section: {(data.get('info') or {}).get('SectionEng', '(none)')}")
    print("  fill in the Features / How to use / Shout outs sections by hand")


if __name__ == "__main__":
    main()
