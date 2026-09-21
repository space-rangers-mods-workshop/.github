"""Assemble a workshop mod's ``mod/`` tree from the readable ``src/`` sources.

The single generic replacement for the per-mod ``build_<mod>.py`` scripts. Run
from the mod folder (or pass the YAML path):

    python build_mod.py ../../DenSettingsControl/DenSettingsControl.yaml

What it does
------------
1. renders ``mod/ModuleInfo.txt`` (UTF-16 LE BOM, LF) from the YAML's ``info``
   block — keys mirror ModuleInfo.txt, multi-line values become repeated ``Key=``
   lines, ``Dependence`` is emitted only when non-empty;
2. encodes the readable sources into ``mod/CFG/*.dat`` (all **signed**), matching
   src files to outputs by convention:

       src/Main*.txt       -> mod/CFG/Main.dat        fmt=HDMain
       src/CacheData*.txt  -> mod/CFG/CacheData.dat   fmt=HDCache
       src/Lang_<Lang>.txt -> mod/CFG/<Lang>/Lang.dat fmt=HDMain   (per language)
       src/Lang*.txt       -> mod/CFG/<Lang>/Lang.dat fmt=HDMain   (one source,
                              emitted for every language declared in info.Languages)

   A source kind the mod does not ship is skipped, not required — a ``Lang``-only
   mod has neither ``Main`` nor ``CacheData``.

3. verifies every ``mod/CFG/**/*.dat`` carries the engine signature;
4. checks that every ``Mods\\<section>\\<mod>\\...`` path the sources reference
   resolves to a file inside ``mod/`` (catches a missing bundled resource), and
   warns when a path's section differs from ``info.SectionEng`` (the mod would
   not find its own files after deployment).

``mod/DATA/`` (textures, models, compiled ``.scr``) is static input — the build
does not create it; a mod-specific adaptation step (bundling resources, compiling
a script) puts it there once and the ``changes`` list records it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# the vendored ranger-tools checkout lives in the workspace root (git-ignored)
for _parent in Path(__file__).resolve().parents:
    _candidate = _parent / 'tools' / 'ranger-tools'
    if _candidate.is_dir():
        sys.path.insert(0, str(_candidate))
        break

import rangers.dat as d  # noqa: E402  (sys.path is fixed above)
import yaml  # noqa: E402

TOOL_NAME = "build_mod.py"
TOOL_VERSION = "1.0.0"

# fmt per output kind (the engine's BlockPar flavours)
FMT_MAIN = "HDMain"
FMT_CACHEDATA = "HDCache"
FMT_LANG = "HDMain"


def read_source_text(src: Path) -> str:
    """Decode a readable source: UTF-16 (BOM stripped) when it has a BOM, else UTF-8."""
    data = src.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def build_module_info(mod: str, info: dict) -> str:
    """Render ModuleInfo.txt from the YAML ``info`` block (canonical layout)."""

    def lines(key):
        value = info.get(key)
        return [] if value is None else [f"{key}={ln}" for ln in str(value).split("\n")]

    head = [
        f"Name={info.get('Name') or mod}",
        f"Author={info.get('Author', '')}",
        f"Conflict={info.get('Conflict', '')}",
    ]
    dependence = info.get("Dependence")
    if dependence:
        head.append(f"Dependence={dependence}")
    head += [
        f"Priority={info.get('Priority', '')}",
        f"Section={info.get('Section', '')}",
        f"SectionEng={info.get('SectionEng', '')}",
        f"Languages={info.get('Languages', '')}",
    ]
    body = (lines("SmallDescription") + lines("SmallDescriptionEng")
            + lines("FullDescription") + lines("FullDescriptionEng"))
    return "\n".join(head + body)


def parse_languages(info: dict) -> list[str]:
    return [x.strip() for x in str(info.get("Languages", "")).split(",") if x.strip()]


def discover_sources(src_dir: Path, languages: list[str]):
    """Map src/*.txt to (out_rel, fmt) by convention. Returns (entries, errors)."""
    files = sorted(p for p in src_dir.glob("*.txt"))
    entries, errors = [], []
    lower = {lang.lower(): lang for lang in languages}

    main_src = next((f for f in files if f.stem.startswith("Main")), None)
    cache_src = next((f for f in files if f.stem.startswith("CacheData")), None)
    if main_src is not None:
        entries.append((main_src, "CFG/Main.dat", FMT_MAIN))
    if cache_src is not None:
        entries.append((cache_src, "CFG/CacheData.dat", FMT_CACHEDATA))

    per_lang: dict[str, Path] = {}
    generic_lang = None
    for f in files:
        if not f.stem.startswith("Lang"):
            continue
        m = re.fullmatch(r"Lang_(.+)", f.stem)
        tag = m.group(1) if m else None
        if tag and tag.lower() in lower:
            per_lang[lower[tag.lower()]] = f
        else:
            generic_lang = generic_lang or f

    for lang in languages:
        src = per_lang.get(lang) or generic_lang
        if src is None:
            errors.append(f"no source for language {lang}")
        else:
            entries.append((src, f"CFG/{lang}/Lang.dat", FMT_LANG))
    if not entries:
        errors.append("nothing to build: no src/*.txt matched Main/CacheData/Lang")
    return entries, errors


def encode(src: Path, out_path: Path, fmt: str, section: str, mod: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.DAT.from_str(read_source_text(src)).to_dat(out_path, fmt=fmt, sign=True)
    b = out_path.read_bytes()
    print(f"[encode] {out_path.name:14} <- {src.name:24} fmt={fmt} signed={d.check_signed(b)} len={len(b)}")


def check_referenced_resources(text: str, mod_dir: Path, section: str, mod: str) -> list[str]:
    """Every ``Mods\\<sec>\\<mod>\\<rest>`` path must resolve inside mod/<rest>."""
    ref = re.compile(r"Mods[\\/]([^\\/]+)[\\/]" + re.escape(mod) + r"[\\/]([^=]+)$", re.I)
    problems = []
    for line in text.splitlines():
        m = ref.search(line.strip())
        if not m:
            continue
        sec, rest = m.group(1), m.group(2).strip()
        if sec.lower() != section.lower():
            problems.append(f"section '{sec}' != info.SectionEng '{section}': {rest}")
        if not (mod_dir / rest.replace("\\", "/")).exists():
            problems.append(f"missing bundled file: {rest}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("yaml_path", help="path to the mod YAML (<mod>/<mod>.yaml)")
    args = parser.parse_args()

    yaml_path = Path(args.yaml_path).resolve()
    root = yaml_path.parent
    src_dir, mod_dir = root / "src", root / "mod"
    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mod = (data.get("mod") or "").strip()
    info = data.get("info") or {}
    if not mod:
        print("ERROR: YAML is missing the 'mod' field")
        raise SystemExit(1)

    entries, errors = discover_sources(src_dir, parse_languages(info))
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        raise SystemExit(1)

    (mod_dir / "ModuleInfo.txt").parent.mkdir(parents=True, exist_ok=True)
    module_info = build_module_info(mod, info)
    (mod_dir / "ModuleInfo.txt").write_bytes(b"\xff\xfe" + module_info.encode("utf-16-le"))
    print(f"[moduleinfo] mod/ModuleInfo.txt <- info ({len(module_info)} chars, UTF-16 LE BOM)")

    section = (info.get("SectionEng") or "").strip()
    for src, out_rel, fmt in entries:
        encode(src, mod_dir / out_rel, fmt, section, mod)

    unsigned = [str(p.relative_to(mod_dir)) for p in mod_dir.rglob("*.dat")
                if not d.check_signed(p.read_bytes())]
    if unsigned:
        print("ERROR: unsigned .dat: " + ", ".join(unsigned))
        raise SystemExit(1)
    print(f"[verify] all {len(list(mod_dir.rglob('*.dat')))} .dat signed")

    cache_src = next((s for s, out, _ in entries if out.startswith("CFG/CacheData")), None)
    if cache_src is not None:
        problems = check_referenced_resources(read_source_text(cache_src), mod_dir, section, mod)
        if problems:
            print("WARNING: resource references:")
            for p in problems:
                print(f"  - {p}")

    print(f"build complete: {mod}")


if __name__ == "__main__":
    main()
