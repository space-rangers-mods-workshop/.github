"""Unpack any mod into a workshop layout: ``mod/`` (byte-exact) + readable ``src/``.

The single generic replacement for a per-mod unpack routine. It drives the
SRHD-XenoModKit CLI and accepts either source kind, auto-detected:

  * a pack tree — a folder holding ``Mods/<Section>/<Mod>`` (e.g. an unpacked
    release under ``origin_artefact/unpacked/<pack>/``) or the mod folder itself;
  * a museum release — a ``.zip`` (or an exhibit folder), flat or under
    ``Mods/<Section>/<Mod>``.

Steps
-----
1. resolve the mod folder (the one with ``ModuleInfo.txt``);
2. stage it byte-exact into ``<out>/mod`` (``srhd.py stage``);
3. decode every ``mod/CFG/**/*.dat`` into readable BlockPar text under ``src/``
   (``srhd.py dat decode``); for a ``Lang.dat`` that trips the toolkit's
   ``Ошибка: Пустое имя блока`` bug it falls back to the BlockParEditor CLI;
4. decompile every ``mod/DATA/Script/*.scr`` into an RScript project under
   ``src/`` (``srhd.py script decompile``).

``mod/DATA/`` binary resources are staged into ``mod/`` (they are package input,
not sources). Mod-specific adaptations (bundling resources from another mod,
compiling a script, fixing a brace) are one-off edits recorded in the YAML's
``changes`` list, not part of this tool.

Usage
-----
    python unpack_mod.py <pack-tree> --mod <Mod> [--out-dir <workshop/<Mod>>]
    python unpack_mod.py <museum-release.zip> --mod <Mod>
    python unpack_mod.py <Mod-folder> --mod <Mod>
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

TOOL_NAME = "unpack_mod.py"
TOOL_VERSION = "1.0.0"

TOOLS_DIR = Path(__file__).resolve().parent
WORKSHOP_DIR = TOOLS_DIR.parents[1]           # workshop/
DEFAULT_XENO_DIR = WORKSHOP_DIR / "SRHD-XenoModKit"
DEFAULT_BLOCKPAR = WORKSHOP_DIR / "BlockParEditor" / "BlockParEditor.exe"


def run(argv: list[str]) -> int:
    print("  $ " + " ".join(str(a) for a in argv))
    return subprocess.run([str(a) for a in argv], check=False).returncode


def find_mod_root(base: Path, mod: str) -> Path | None:
    """Return the folder holding ModuleInfo.txt within ``base``, or None."""
    if (base / "ModuleInfo.txt").is_file():
        return base
    for candidate in sorted(base.glob(f"Mods/*/{mod}")):
        if (candidate / "ModuleInfo.txt").is_file():
            return candidate
    return None


def resolve_mod_source(source: Path, mod: str) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    """Resolve any accepted source into the folder that holds ModuleInfo.txt."""
    source = source.resolve()
    if source.is_dir():
        root = find_mod_root(source, mod)
        if root is None:
            print(f"ERROR: no ModuleInfo.txt found under {source} (looking for Mods/*/{mod})")
            raise SystemExit(1)
        return root, None
    if source.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="unpack_mod_")
        with zipfile.ZipFile(source) as zf:
            zf.extractall(tmp.name)
        root = find_mod_root(Path(tmp.name), mod)
        if root is None:
            print(f"ERROR: no ModuleInfo.txt inside {source}")
            raise SystemExit(1)
        return root, tmp
    print(f"ERROR: source must be a folder or a .zip: {source}")
    raise SystemExit(1)


def decode_target(cfg_dat: Path, cfg_dir: Path) -> str:
    """src/ name for a CFG .dat — Lang.dat keeps its language: Lang_Rus.txt."""
    if cfg_dat.stem == "Lang" and cfg_dat.parent != cfg_dir:
        return f"Lang_{cfg_dat.parent.name}"
    return cfg_dat.stem


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="pack tree, mod folder, or museum .zip")
    parser.add_argument("--mod", required=True, help="mod name (the repo folder id)")
    parser.add_argument("--out-dir", help="workshop mod folder (default: workshop/<mod>)")
    parser.add_argument("--xeno-dir", default=str(DEFAULT_XENO_DIR), help=f"SRHD-XenoModKit folder (default: {DEFAULT_XENO_DIR})")
    parser.add_argument("--blockpar", default=str(DEFAULT_BLOCKPAR), help="BlockParEditor.exe used as the Lang.dat fallback")
    args = parser.parse_args()

    xeno = Path(args.xeno_dir)
    srhd = xeno / "srhd.py"
    blockpar = Path(args.blockpar)
    out_dir = Path(args.out_dir) if args.out_dir else WORKSHOP_DIR / args.mod
    mod_dir, tmp = resolve_mod_source(Path(args.source), args.mod)
    src_dir = out_dir / "src"
    print(f"{TOOL_NAME}: {mod_dir} -> {out_dir}")

    if not srhd.is_file():
        print(f"ERROR: SRHD-XenoModKit not found at {srhd} (see --xeno-dir)")
        raise SystemExit(1)

    # 1. stage the byte-exact mod/ tree
    if out_dir.joinpath("mod").exists():
        shutil.rmtree(out_dir / "mod")
    out_dir.mkdir(parents=True, exist_ok=True)
    if run([sys.executable, "-B", srhd, "stage", mod_dir, out_dir / "mod"]) != 0:
        print("ERROR: stage failed")
        raise SystemExit(1)

    # 2. decode every CFG .dat into src/
    src_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = out_dir / "mod" / "CFG"
    for cfg_dat in sorted(cfg_dir.rglob("*.dat")) if cfg_dir.is_dir() else []:
        out_txt = src_dir / f"{decode_target(cfg_dat, cfg_dir)}.txt"
        if run([sys.executable, "-B", srhd, "dat", "decode", cfg_dat, out_txt]) == 0:
            continue
        print(f"  ! dat decode failed for {cfg_dat.name}; trying BlockParEditor")
        if not blockpar.is_file():
            print(f"ERROR: {cfg_dat.name} needs the BlockParEditor fallback, not found: {blockpar}")
            raise SystemExit(1)
        if run([blockpar, "--cli", "--convert", cfg_dat, out_txt]) != 0:
            print(f"ERROR: BlockParEditor could not convert {cfg_dat.name}")
            raise SystemExit(1)

    # 3. decompile every scenario .scr into src/
    script_dir = out_dir / "mod" / "DATA" / "Script"
    for scr in sorted(script_dir.glob("*.scr")) if script_dir.is_dir() else []:
        rson = src_dir / f"{scr.stem}.rson"
        lang_dat = cfg_dir / "Rus" / "Lang.dat"
        argv = [sys.executable, "-B", srhd, "script", "decompile", scr, rson]
        if lang_dat.is_file():
            argv += ["--lang-dat", lang_dat]
        run(argv)

    if tmp is not None:
        tmp.cleanup()
    print(f"unpack complete: {out_dir}")


if __name__ == "__main__":
    main()
