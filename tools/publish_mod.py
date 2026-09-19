"""Publish a mod to the workshop — one command for the whole chain.

What this does
--------------
Reads a single mod YAML and runs the publish chain in strict order, writing a
log line for every step and stopping on the first failed step:

  1. resolve the input — the single mod YAML ``<mod>/<mod>.yaml`` (``mod``,
     ``info``, ``based_on``), passed as the positional argument; its own folder
     is the mod repository folder;
  2. sources — the readable sources unpacked/decompiled from the museum archive
     are consumed as-is; unpacking is a separate process with separate tools and
     is out of scope here;
  3. generate the card README (``generate_card.py``: ``based_on`` + ``info``,
     ``## 🔗 Based on`` when ``based_on`` non-empty, CC BY-NC-SA 4.0 badge) and
     the ``LICENSE`` file (from ``template/LICENSE``: a plain-text attribution
     block with the author list and source links, plus the full CC BY-NC-SA
     4.0 legal code);
  4. form the local repository folder (card + license + the mod YAML — the
     single source already lives in the folder — + a generated ``.gitignore``;
     an existing dev repo is kept as-is, only the missing LICENSE is added);
  5. initialize the local git repository (``git init`` + commit) — for an
     existing dev repo this just commits the new LICENSE; a
     purely local, safe step, so the later ``gh`` push has something to push;
  6. update the showcase locally (``update_showcase.py``: append the mod to
     ``mods.csv`` and rebuild the showcase main page in ``.github``) — local,
     safe, not yet pushed;
  7. publish the repository through ``gh`` (``gh repo create`` + push,
     description set from the YAML's ``info.SmallDescriptionEng``), package the
     assembled ``mod/`` folder into ``{mod}.zip`` under its own path in the game
     tree (``Mods/<SectionEng>/{mod}/…``, ``Miscellaneous`` for a workshop mod)
     and create the release (``gh release create`` with that archive,
     first release always ``v2.0.0``, title = mod name);
  8. commit & push the showcase changes (``git add/commit/push`` in ``.github``)
     — only after step 7 succeeded, so the pushed page links to a live repo.

Pass ``--no-publish`` to run steps 1-6 only (card + license -> repo folder ->
local git init -> showcase local update) without touching ``gh`` or the remote —
useful for a local verification run.

Usage
-----
    # run from workshop/.github/tools; the YAML is the single source, its
    # folder (../../AMod_Spacejunk) is the mod repo folder
    python publish_mod.py ../../AMod_Spacejunk/AMod_Spacejunk.yaml

    # local-only run (no gh, no remote):
    python publish_mod.py ../../AMod_Spacejunk/AMod_Spacejunk.yaml --no-publish
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

from generate_card import strip_conditional_blocks

TOOL_NAME = "publish_mod.py"
TOOL_VERSION = "1.4.0"
DEFAULT_ORG = "space-rangers-mods-workshop"
RELEASE_VERSION = "v2.0.0"  # first workshop release; subsequent releases are bumped upward
# the game's inline <color=...> markup is dropped from the repo description
_COLOR_TAG_RE = re.compile(r"</?color(?:=[^>]*)?>", re.IGNORECASE)
# A workshop mod is not tied to the section of the pack it came from — the yaml's
# info.SectionEng (Miscellaneous) is where it deploys, the same value the card prints.
WORKSHOP_SECTION = "Miscellaneous"

TOOLS_DIR = Path(__file__).resolve().parent
SHOWCASE_DIR = TOOLS_DIR.parent  # workshop/.github — the showcase repo local working copy
LICENSE_TEMPLATE_PATH = SHOWCASE_DIR / "template" / "LICENSE"


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
    """Fill the CC BY-NC-SA 4.0 ``LICENSE`` from ``template/LICENSE``.

    The derivation attribution paragraph in the template is wrapped in a
    ``{{#HAS_BASED_ON}} ... {{/HAS_BASED_ON}}`` block, mirroring the card. When
    the mod is derived from museum exhibits / other sources (``based_on``
    non-empty) the block is kept and each source is credited with a label and a
    link; when it is made without such sources the block is removed, so the
    license never claims an origin that does not exist — the license then covers
    the author's own work.
    """
    template = LICENSE_TEMPLATE_PATH.read_text(encoding="utf-8")
    flags = {"HAS_BASED_ON": bool(based_on)}
    template = strip_conditional_blocks(template, flags)

    # The input sources may carry a leading provenance marker (e.g. 🏛️ for a
    # museum-derived source); strip it so the license lists clean URLs.
    sources = "\n".join(
        f"- {e.get('note', '')} — {e.get('source', '').replace('🏛️ ', '').replace('🏛️', '')}"
        for e in based_on
    )

    return (
        template
        .replace("{{MOD}}", mod)
        .replace("{{AUTHOR}}", author)
        .replace("{{REPOSITORY}}", repository)
        .replace("{{MUSEUM_EXHIBITS}}", sources)
    )


def build_mod_archive(out_dir: Path, mod: str, section: str) -> Path:
    """Zip the assembled ``mod/`` folder under its chain in the game tree.

    The release archive keeps the mod's own path — ``Mods/<section>/<mod>/…`` — so
    a user unpacks it straight into the game folder and the category survives the
    round trip; ``section`` is the mod's ``info.SectionEng`` (``Miscellaneous`` for
    a workshop mod). ``*.zip`` is gitignored in the dev repo, so the archive is
    never committed.
    """
    mod_dir = out_dir / "mod"
    if not mod_dir.is_dir():
        raise StepFailed(f"cannot package release archive: {mod_dir} is not a directory")
    zip_path = out_dir / f"{mod}.zip"
    root = f"Mods/{section}/{mod}"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(mod_dir.rglob("*")):
            if path.is_dir():
                continue
            zf.write(path, f"{root}/{path.relative_to(mod_dir).as_posix()}")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("yaml_path", help="path to the mod YAML (<mod>/<mod>.yaml — the single source, inside the mod repo folder)")
    parser.add_argument("--out-dir", help="local repository folder for the mod (default: the mod YAML's own folder — yaml_path.parent)")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"workshop org (default: {DEFAULT_ORG})")
    parser.add_argument("--version", default=RELEASE_VERSION, help=f"release version (default: {RELEASE_VERSION}, the first release; bump for subsequent releases)")
    parser.add_argument("--notes", default="", help="release notes (markdown); empty = no notes")
    parser.add_argument("--notes-file", help="read the release notes from a file (overrides --notes)")
    parser.add_argument("--no-publish", action="store_true", help="run card+license -> repo folder -> local git init -> showcase local update only (no gh, no remote, no showcase push)")
    parser.add_argument("--log", help="path to the pipeline log file (default: <out-dir>/publish.log)")
    args = parser.parse_args()

    notes = args.notes
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8")

    with open(args.yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mod = (data.get("mod") or "").strip()
    if not mod:
        print("ERROR: YAML is missing the 'mod' field")
        raise SystemExit(1)
    info = data.get("info") or {}
    author = (info.get("Author") or "").strip()
    summary = _COLOR_TAG_RE.sub("", info.get("SmallDescriptionEng") or "").strip()
    section = (info.get("SectionEng") or "").strip() or WORKSHOP_SECTION
    based_on = data.get("based_on") or []

    # Default out-dir: the mod YAML's own folder. The YAML is the single source
    # and already lives inside the mod repo folder, so that folder IS the repo —
    # nothing is copied in, and the repo is never nested inside the showcase.
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(args.yaml_path).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    is_existing_repo = (out_dir / ".git").is_dir()
    log_path = Path(args.log) if args.log else out_dir / "publish.log"

    log_write(log_path, f"START publish {mod} ({TOOL_NAME} {TOOL_VERSION})")
    print(f"publish {mod}: chain start")

    # 3. Generate the card README (workshop has no manifest — the files section
    #    is left empty; no extract tool) and the LICENSE. For an existing dev
    #    repo the card README may carry hand-written sections (e.g. a mod
    #    evolution log) that the template cannot reproduce, so it is kept as-is
    #    and only the missing LICENSE is written.
    if (out_dir / "README.md").exists():
        log_write(log_path, "card: README.md already present — kept as-is")
        print("[card] README.md already present — kept as-is")
    else:
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

    # 4. Form the local repository folder — the source files of the mod. The
    #    mod YAML (the single source) already lives here — it is the folder's
    #    own input, not copied in. An existing dev repo keeps a hand-tuned
    #    ``.gitignore``, so it is only written when missing; the pipeline log
    #    (``.log``) is excluded either way.
    if not (out_dir / ".gitignore").exists():
        (out_dir / ".gitignore").write_text(
            "# build/package artifacts\n*.zip\n*.log\n\n# scratch\ntmp/\n.ruff_cache/\n",
            encoding="utf-8",
        )

    required = ["README.md", "LICENSE", Path(args.yaml_path).name, ".gitignore"]
    missing = [name for name in required if not (out_dir / name).exists()]
    if missing:
        log_write(log_path, f"FAIL repo-folder: missing {missing}")
        raise StepFailed(f"repository folder incomplete, missing: {missing}")
    log_write(log_path, f"OK repo-folder: {out_dir} ({', '.join(required)})")
    print(f"[repo-folder] {out_dir}")

    # 5. Local git repository — safe, purely local step: initialize the repo
    #    and make the initial commit, so the later ``gh repo create --push``
    #    (step 7) has something to push. An existing dev repo is already a git
    #    repo on ``main``, so init is skipped and only the newly added LICENSE
    #    is committed (a no-op commit would fail with exit 1).
    if not is_existing_repo:
        run_step(log_path, "git-init", ["git", "-C", str(out_dir), "init", "-b", "main"])
    run_step(log_path, "git-add", ["git", "-C", str(out_dir), "add", "-A"])
    staged = subprocess.run(
        ["git", "-C", str(out_dir), "diff", "--cached", "--quiet"],
        check=False, capture_output=True, text=True,
    )
    if staged.returncode != 0:
        run_step(log_path, "git-commit", ["git", "-C", str(out_dir), "commit", "-m", f"Add {mod} mod"])
    else:
        log_write(log_path, "git: nothing staged — dev repo already clean")
        print("git: nothing staged — dev repo already clean")

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

    # 7. Publish the repository through gh and create the release. The mod
    #    summary (from the input YAML's ``info.SmallDescriptionEng``) becomes
    #    the repo description, so the new repo is not an empty "No description"
    #    placeholder. GitHub caps descriptions at 350 chars, so the summary is
    #    truncated to fit. When the repo already exists (a subsequent release)
    #    the create step is skipped and the current branch is pushed instead.
    repo_exists = subprocess.run(
        ["gh", "repo", "view", f"{args.org}/{mod}", "--json", "name"],
        check=False, capture_output=True, text=True,
    ).returncode == 0
    if repo_exists:
        remotes = subprocess.run(
            ["git", "-C", str(out_dir), "remote"], check=False, capture_output=True, text=True,
        ).stdout.split()
        if "origin" not in remotes:
            run_step(log_path, "git-remote", ["git", "-C", str(out_dir), "remote", "add", "origin", f"git@github.com:{args.org}/{mod}.git"])
        run_step(log_path, "git-push", ["git", "-C", str(out_dir), "push", "-u", "origin", "HEAD"])
    else:
        create_cmd = ["gh", "repo", "create", f"{args.org}/{mod}", "--public", "--source", str(out_dir), "--push"]
        if summary:
            create_cmd += ["--description", summary[:350]]
        run_step(log_path, "gh-create-repo", create_cmd)
    #    Package the assembled ``mod/`` folder into the release archive first
    #    (the mod's own path in the game tree — ``Mods/<SectionEng>/<mod>/`` — so
    #    it unpacks straight into the game folder), then attach
    #    it to the release. ``--repo`` pins the release to the mod repo: without
    #    it ``gh`` targets the repo of the current directory, which for this
    #    tool is the showcase ``.github`` working copy — the release would ship
    #    to the wrong repo.
    zip_path = build_mod_archive(out_dir, mod, section)
    log_write(log_path, f"OK archive: {zip_path}")
    print(f"[archive] {zip_path}")
    release_cmd = ["gh", "release", "create", args.version, "--repo", f"{args.org}/{mod}", "--title", mod]
    if notes:
        release_cmd += ["--notes", notes]
    release_cmd.append(str(zip_path))
    run_step(log_path, "gh-release", release_cmd)

    # 8. Showcase — commit & push. Side-effect step: the updated ``mods.csv``
    #    and main page (step 6) point to the mod repo, which now exists after
    #    step 7, so the pushed page never links to a missing repo.
    #    ``update_showcase`` is idempotent — on a re-run of an already-listed
    #    mod there is nothing staged, so commit/push are skipped (a no-op
    #    commit would fail with exit 1). ``profile/README.md`` is staged too so
    #    the org profile reflects the new mod.
    run_step(
        log_path,
        "showcase-add",
        ["git", "-C", str(SHOWCASE_DIR), "add", "mods.csv", "README.md", "profile/README.md"],
    )
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
