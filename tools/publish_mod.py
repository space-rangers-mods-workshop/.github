"""Publish a mod to the workshop — one command for the whole chain.

What this does
--------------
Reads a single mod YAML and runs the publish chain in strict order, writing a
log line for every step and stopping on the first failed step:

  1. resolve the input — ``mods/<mod>.yaml`` (``mod``, ``info``, ``based_on``);
  2. sources — the readable sources unpacked/decompiled from the museum archive
     are consumed as-is; unpacking is a separate process with separate tools and
     is out of scope here;
  3. generate the card README (``generate_card.py``: ``based_on`` + ``info``,
     ``## 🏛️ Original`` when ``based_on`` non-empty, CC BY-NC-SA 4.0 badge) and
     the ``LICENSE`` file (from ``template/LICENSE.md``: attribution with the
     author list and museum-exhibit links, plus the full CC BY-NC-SA 4.0 legal
     code);
  4. form the local repository folder (card + license + a copy of the mod YAML +
     a generated ``.gitignore``);
  5. initialize the local git repository (``git init`` + initial commit) — a
     purely local, safe step, so the later ``gh`` push has something to push;
  6. update the showcase locally (``update_showcase.py``: append the mod to
     ``mods.csv`` and rebuild the showcase main page in ``.github``) — local,
     safe, not yet pushed;
  7. publish the repository through ``gh`` (``gh repo create`` + push) and
     create the release (``gh release create``, first release always ``v2.0.0``,
     title = mod name);
  8. commit & push the showcase changes (``git add/commit/push`` in ``.github``)
     — only after step 7 succeeded, so the pushed page links to a live repo.

Pass ``--no-publish`` to run steps 1-6 only (card + license -> repo folder ->
local git init -> showcase local update) without touching ``gh`` or the remote —
useful for a local verification run.

Usage
-----
    python publish_mod.py mods/AMod_Spacejunk.yaml \
        --out-dir ../../AMod_Spacejunk

    # local-only run (no gh, no remote):
    python publish_mod.py mods/AMod_Spacejunk.yaml \
        --out-dir ./build --no-publish
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from generate_card import strip_conditional_blocks

TOOL_NAME = "publish_mod.py"
TOOL_VERSION = "1.0.0"
DEFAULT_ORG = "space-rangers-mods-workshop"
MUSEUM_ORG = "space-rangers-mods-museum"
RELEASE_VERSION = "v2.0.0"  # first workshop release; subsequent releases are bumped upward

TOOLS_DIR = Path(__file__).resolve().parent
SHOWCASE_DIR = TOOLS_DIR.parent  # workshop/.github — the showcase repo local working copy
WORKSHOP_DIR = SHOWCASE_DIR.parent
LICENSE_TEMPLATE_PATH = SHOWCASE_DIR / "template" / "LICENSE.md"


class StepFailed(RuntimeError):
    """A pipeline step failed; the chain must stop here."""


def log_write(log_path: Path, line: str) -> None:
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {line}\n")


def run_step(log_path: Path, name: str, argv: list[str]) -> None:
    """Run a step, log it, and stop the chain on failure."""
    log_write(log_path, f"STEP {name}: {' '.join(argv)}")
    print(f"[{name}] {' '.join(argv)}")
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
    except OSError as exc:
        log_write(log_path, f"FAIL {name}: cannot run: {exc}")
        raise StepFailed(f"cannot run step '{name}': {exc}")
    if result.stdout:
        log_write(log_path, "  " + result.stdout.strip().replace("\n", "\n  "))
        print(result.stdout.rstrip())
    if result.returncode != 0:
        err = result.stderr.strip()
        log_write(log_path, f"FAIL {name}: exit {result.returncode}: {err}")
        if err:
            print(err, file=sys.stderr)
        raise StepFailed(f"step '{name}' failed (exit {result.returncode})")


def render_license(mod: str, author: str, org: str, based_on: list, repository: str) -> str:
    """Fill the CC BY-NC-SA 4.0 ``LICENSE`` from ``template/LICENSE.md``.

    The museum-derivation attribution paragraph in the template is wrapped in
    a ``{{#HAS_BASED_ON}} ... {{/HAS_BASED_ON}}`` block, mirroring the card. When
    the mod is forked from museum exhibits (``based_on`` non-empty) the block is
    kept and each exhibit is credited with a museum link; when it is made without
    museum sources the block is removed, so the license never claims a museum
    origin that does not exist — the license then covers the author's own work.
    """
    template = LICENSE_TEMPLATE_PATH.read_text(encoding="utf-8")
    flags = {"HAS_BASED_ON": bool(based_on)}
    template = strip_conditional_blocks(template, flags)

    museum = "\n".join(
        f"- **{e.get('museum_mod', '')}** — "
        f"[release {e.get('museum_release', '')}](https://github.com/{MUSEUM_ORG}/{e.get('museum_mod', '')})"
        for e in based_on
    )

    return (
        template
        .replace("{{MOD}}", mod)
        .replace("{{AUTHOR}}", author)
        .replace("{{REPOSITORY}}", repository)
        .replace("{{MUSEUM_EXHIBITS}}", museum)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("yaml_path", help="path to the mod YAML (mods/<mod>.yaml)")
    parser.add_argument("--out-dir", help="local repository folder for the mod (default: <workshop working dir>/<mod>, flat next to .github)")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"workshop org (default: {DEFAULT_ORG})")
    parser.add_argument("--version", default=RELEASE_VERSION, help=f"release version (default: {RELEASE_VERSION}, the first release; bump for subsequent releases)")
    parser.add_argument("--no-publish", action="store_true", help="run card+license -> repo folder -> local git init -> showcase local update only (no gh, no remote, no showcase push)")
    parser.add_argument("--log", help="path to the pipeline log file (default: <out-dir>/publish.log)")
    args = parser.parse_args()

    with open(args.yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mod = (data.get("mod") or "").strip()
    if not mod:
        print("ERROR: YAML is missing the 'mod' field")
        raise SystemExit(1)
    info = data.get("info") or {}
    author = (info.get("author") or "").strip()
    based_on = data.get("based_on") or []

    # Default out-dir: the workshop working dir (parent of the .github showcase
    # repo), so mod repos sit flat next to .github — never nested inside the
    # showcase repo.
    out_dir = Path(args.out_dir) if args.out_dir else WORKSHOP_DIR / mod
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log) if args.log else out_dir / "publish.log"

    log_write(log_path, f"START publish {mod} ({TOOL_NAME} {TOOL_VERSION})")
    print(f"publish {mod}: chain start")

    # 3. Generate the card README (workshop has no manifest — the files section
    #    is left empty; no extract tool) and the LICENSE.
    run_step(
        log_path,
        "card",
        [
            sys.executable, str(TOOLS_DIR / "generate_card.py"),
            "--yaml", str(Path(args.yaml_path)),
            "--out", str(out_dir / "README.md"),
            "--org", args.org,
        ],
    )
    license_text = render_license(
        mod, author, args.org, based_on,
        f"https://github.com/{args.org}/{mod}",
    )
    (out_dir / "LICENSE").write_text(license_text, encoding="utf-8")
    log_write(log_path, f"OK license: {out_dir / 'LICENSE'}")
    print(f"[license] {out_dir / 'LICENSE'}")

    # 4. Form the local repository folder — the source files of the mod.
    #    Copy the mod YAML in first: it records where the instance came from
    #    and lives with the mod. The pipeline log (``.log``) is excluded.
    shutil.copy2(args.yaml_path, out_dir / Path(args.yaml_path).name)
    (out_dir / ".gitignore").write_text("*.log\n", encoding="utf-8")

    required = ["README.md", "LICENSE", Path(args.yaml_path).name, ".gitignore"]
    missing = [name for name in required if not (out_dir / name).exists()]
    if missing:
        log_write(log_path, f"FAIL repo-folder: missing {missing}")
        raise StepFailed(f"repository folder incomplete, missing: {missing}")
    log_write(log_path, f"OK repo-folder: {out_dir} ({', '.join(required)})")
    print(f"[repo-folder] {out_dir}")

    # 5. Local git repository — safe, purely local step: initialize the repo
    #    and make the initial commit, so the later ``gh repo create --push``
    #    (step 7) has something to push.
    run_step(log_path, "git-init", ["git", "-C", str(out_dir), "init"])
    run_step(log_path, "git-add", ["git", "-C", str(out_dir), "add", "-A"])
    run_step(log_path, "git-commit", ["git", "-C", str(out_dir), "commit", "-m", f"Add {mod} mod"])

    # 6. Showcase — local update. Safe, local step: append the mod to the
    #    workshop mod list ``mods.csv`` and rebuild the showcase main page in
    #    ``workshop/.github`` — nothing is pushed yet. The pushed page (step 8)
    #    will link to the mod repo, which only exists after step 7.
    run_step(
        log_path,
        "showcase-local",
        [sys.executable, str(TOOLS_DIR / "update_showcase.py"), "--mod", mod],
    )

    if args.no_publish:
        log_write(log_path, "DONE (no-publish): chain stopped after local git repo + showcase local update")
        print("no-publish: stopped after local git repo + showcase local update — gh publish step skipped")
        return

    # 7. Publish the repository through gh and create the release.
    run_step(
        log_path,
        "gh-create-repo",
        ["gh", "repo", "create", f"{args.org}/{mod}", "--public", "--source", str(out_dir), "--push"],
    )
    #    ``--repo`` pins the release to the mod repo: without it ``gh`` targets
    #    the repo of the current directory, which for this tool is the showcase
    #    ``.github`` working copy — the release would ship to the wrong repo.
    run_step(
        log_path,
        "gh-release",
        ["gh", "release", "create", args.version, "--repo", f"{args.org}/{mod}", "--title", mod],
    )

    # 8. Showcase — commit & push. Side-effect step: the updated ``mods.csv``
    #    and main page (step 6) point to the mod repo, which now exists after
    #    step 7, so the pushed page never links to a missing repo.
    #    ``update_showcase`` is idempotent — on a re-run of an already-listed
    #    mod there is nothing staged, so commit/push are skipped (a no-op
    #    commit would fail with exit 1).
    run_step(log_path, "showcase-add", ["git", "-C", str(SHOWCASE_DIR), "add", "mods.csv", "README.md"])
    staged = subprocess.run(
        ["git", "-C", str(SHOWCASE_DIR), "diff", "--cached", "--quiet"],
        check=False, capture_output=True, text=True,
    )
    if staged.returncode != 0:
        run_step(log_path, "showcase-commit", ["git", "-C", str(SHOWCASE_DIR), "commit", "-m", f"showcase: add {mod}"])
        run_step(log_path, "showcase-push", ["git", "-C", str(SHOWCASE_DIR), "push"])
    else:
        log_write(log_path, "showcase: no changes — already up to date")
        print("showcase: no changes — already up to date")

    log_write(log_path, "DONE publish complete")
    print(f"publish {mod}: done")


if __name__ == "__main__":
    try:
        main()
    except StepFailed as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
