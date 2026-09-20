"""Unpack any mod into a workshop layout: ``mod/`` (byte-exact) + readable ``src/``.

The single generic replacement for a per-mod unpack routine. It accepts either
source kind, auto-detected:

  * a pack tree — a folder holding ``Mods/<Section>/<Mod>`` (e.g. an unpacked
    release under ``origin_artefact/unpacked/<pack>/``) or the mod folder itself;
  * a museum release — a ``.zip`` (or an exhibit folder), flat or under
    ``Mods/<Section>/<Mod>``.

Steps
-----
1. resolve the mod folder (the one with ``ModuleInfo.txt``);
2. stage it byte-exact into ``<out>/mod`` (``srhd.py stage``);
3. decode every ``mod/CFG/**/*.dat`` into readable BlockPar text under ``src/``
   with the vendored **ranger-tools** codec (``rangers.dat``, fmt picked by file
   name) — it also verifies the file's stored content hash, so a success means
   the mod's own ``.dat`` is self-consistent; a file it refuses (a hash the mod
   author got wrong, e.g. ``ExpPanel``'s ``Main.dat``) is decoded by
   ``srhd.py dat decode`` instead;
4. decompile every ``mod/DATA/Script/*.scr`` into an RScript project under
   ``src/`` (``srhd.py script decompile`` — of the two available decoders the
   toolkit recovers the wider range: 9 of 9 mod scripts here against 7 of 9 for
   ``rangers.scr``, which refuses the older format 6, and it reports whether the
   SCR -> RSON -> SCR round trip passed). Its two non-zero outcomes are handled
   rather than ignored: exit 2 means the copy was recovered but not verified, and
   it is kept (``--keep-unverified``) with a note; exit 1 means RScript itself
   failed and the step is retried with ``--fallback-without-lang``, which drops
   the dialog import but still round-trip-checks the result. Either way a
   readable ``<Mod>.rson`` lands in ``src/``.

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
TOOL_VERSION = "1.1.0"

TOOLS_DIR = Path(__file__).resolve().parent
WORKSHOP_DIR = TOOLS_DIR.parents[1]           # workshop/
ROOT_DIR = TOOLS_DIR.parents[2]               # workspace root
DEFAULT_XENO_DIR = ROOT_DIR / "tools" / "SRHD-XenoModKit"

# the vendored ranger-tools checkout (its upstream is external, git-ignored)
sys.path.insert(0, str(ROOT_DIR / "tools" / "ranger-tools"))
import rangers.dat  # noqa: E402  (sys.path is fixed above)

# the engine's BlockPar flavours, by file name — auto-detection is unreliable
RANGER_FMT = {"main": "HDMain", "cachedata": "HDCache", "lang": "HDMain"}


def decode_dat(path: Path, out_txt: Path) -> bool:
    """Decode one .dat with ranger-tools; False when it does not accept the file.

    ``to_txt`` truncates the output before it renders the tree, so a failure can
    leave an empty file behind — remove it, or the toolkit fallback (which
    refuses to overwrite) cannot write its own result.
    """
    fmt = RANGER_FMT.get(path.stem.casefold())
    try:
        rangers.dat.DAT.from_dat(path, fmt=fmt).to_txt(out_txt)
    except Exception:  # noqa: BLE001  (not BlockPar, or a mismatched content hash)
        out_txt.unlink(missing_ok=True)
        return False
    return True


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
    args = parser.parse_args()

    xeno = Path(args.xeno_dir)
    srhd = xeno / "srhd.py"
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

    # 2. decode every CFG .dat into src/ — ranger-tools first, the toolkit for what it refuses
    src_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = out_dir / "mod" / "CFG"
    undecoded: list[str] = []
    for cfg_dat in sorted(cfg_dir.rglob("*.dat")) if cfg_dir.is_dir() else []:
        out_txt = src_dir / f"{decode_target(cfg_dat, cfg_dir)}.txt"
        if decode_dat(cfg_dat, out_txt):
            print(f"  {cfg_dat.relative_to(cfg_dir)} -> {out_txt.name} (ranger-tools)")
            continue
        print(f"  ! ranger-tools refused {cfg_dat.name}; decoding with SRHD-XenoModKit")
        if run([sys.executable, "-B", srhd, "dat", "decode", cfg_dat, out_txt]) != 0:
            # One odd file must not cost the whole unpack (the toolkit's own decode can need
            # tools/BlockParEditor for it); name it at the end instead.
            out_txt.unlink(missing_ok=True)
            undecoded.append(cfg_dat.relative_to(cfg_dir).as_posix())

    # 3. decompile every scenario .scr into src/
    #    The toolkit reports exit 2 when it recovered the script but the SCR -> RSON -> SCR
    #    round trip failed and the copy was kept unverified (--keep-unverified), and exits 1
    #    when RScript itself failed (e.g. a Lang.dat it cannot import) — that one is retried
    #    with --fallback-without-lang, which drops the dialog import but still verifies.
    script_dir = out_dir / "mod" / "DATA" / "Script"
    for scr in sorted(script_dir.glob("*.scr")) if script_dir.is_dir() else []:
        rson = src_dir / f"{scr.stem}.rson"
        lang_dat = cfg_dir / "Rus" / "Lang.dat"
        base = [sys.executable, "-B", srhd, "script", "decompile"]
        if lang_dat.is_file():
            base += ["--lang-dat", lang_dat]
        code = 1
        with tempfile.TemporaryDirectory() as staging:
            # Only a recovered-but-unverified copy goes to the staging path: a
            # round-trip-checked RSON lands on its canonical path directly (the project
            # file records the path it was written to, so it must be the real one).
            unverified = Path(staging) / rson.name
            for extra in ([], ["--fallback-without-lang"]):
                code = run(base + [scr, rson, "--keep-unverified", unverified, "--overwrite", *extra])
                if code in (0, 2):
                    break
                rson.unlink(missing_ok=True)
                print(f"  {scr.name}: RScript failed; retrying without the dialog import")
            if code == 2:
                unverified.replace(rson)
        if code not in (0, 2):
            rson.unlink(missing_ok=True)
            print(f"ERROR: script decompile failed for {scr.name}")
            raise SystemExit(1)
        print(f"  {scr.name} -> {rson.name} ({'verified' if code == 0 else 'unverified, round trip failed'})")

    if tmp is not None:
        tmp.cleanup()
    if undecoded:
        print(f"unpack complete (with gaps): {out_dir}")
        for name in undecoded:
            print(f"  ! no source text for CFG/{name}")
    else:
        print(f"unpack complete: {out_dir}")


if __name__ == "__main__":
    main()
