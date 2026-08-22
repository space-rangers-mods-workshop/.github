# Mod publish workflow

End-to-end publish chain for shipping a mod to the `space-rangers-mods-workshop` workshop.
One run = one mod; each step starts only after the previous one completes. This is the dynamic
counterpart of the [museum workflow](../museum/.github/workflow.md): a mod may be forked from a
museum exhibit, adapted, and released as evolving versions starting at `v2.0.0`, or developed from
scratch and released the same way.

The input — `mods/<mod>.yaml` (`based_on` + `info`) — feeds the pipeline. `based_on` is an optional
list of the museum exhibits (zero or more) the mod is forked from; for a forked mod the readable
sources come from those museum archives through a **separate process and separate tools**
(unpack/decompile), which is out of scope of this document — this workflow consumes their output.

## Two GitHub organizations — do not conflate them

- **Museum (archive):** `space-rangers-mods-museum` — static snapshots, release always `v1.0.0`,
  license All Rights Reserved / Fair Use.
- **Workshop (development):** `space-rangers-mods-workshop` — developed mods (forked from the museum
  or built from scratch), releases from `v2.0.0`, license CC BY-NC-SA 4.0. Originals of forked mods
  are always looked up in the museum.

Three GitHub entities in the workshop:

- **Organization:** `space-rangers-mods-workshop` — hosts every mod repo and the showcase.
- **Mod repository:** `space-rangers-mods-workshop/<mod>` — one repo per mod; created by this
  workflow.
- **Showcase repository:** `space-rangers-mods-workshop/.github` — a single separate repo holding
  the shared tools, the `.csv` mod list and the main showcase page built from that `.csv`; its local
  working copy is `workshop/.github`, where this file and the templates live.

## Full chain — one command

| input                                             | command                                                             | output                                                                                  |
|---------------------------------------------------|---------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| filled `mods/<mod>.yaml` (`based_on` + `info`) + sources | `python tools/publish_mod.py mods/<mod>.yaml`                       | mod repo `space-rangers-mods-workshop/<mod>` created + pushed; release `v2.0.0` (first)  |

Steps are ordered **safe-first, side-effects last**: every locally executable step comes before
anything that touches the remote org. With `--no-publish` the chain stops after all safe local steps
(local showcase update included); without it continues to the `gh` publish and the showcase push.

| step                      | phase       | output                                                                                                   |
|---------------------------|-------------|----------------------------------------------------------------------------------------------------------|
| 1. Input — mod YAML       | safe        | `mods/<mod>.yaml` with `based_on` + `info` (from `template/mod-input.yaml`)                              |
| 2. Sources                | safe        | readable sources (for a forked mod, unpacked from the museum archive) — **separate process/tools** (consumed here)            |
| 3. Card + license          | safe        | `README.md` from `template/mod-card.md` (`based_on` + `info`, `## 🔗 Based on` when `based_on` non-empty, CC BY-NC-SA 4.0 badge) + `LICENSE` from `template/LICENSE.md` (attribution + full legal code). For an existing dev repo the card is kept as-is — only the missing `LICENSE` is written |
| 4. Local repository       | safe        | repo folder: `README.md`, `LICENSE`, `<mod>.yaml`, `.gitignore` (an existing dev repo keeps its own YAML and `.gitignore` — only missing files are written)                                             |
| 5. Local git repository   | safe        | `git init` + initial commit (so `gh repo create --source --push` has something to push). For an existing dev repo init is skipped and only the newly added `LICENSE` is committed                   |
| 6. Showcase — local update| safe        | `.csv` row + main page rebuilt in `workshop/.github` (local, not yet pushed)                               |
| 7. Publish mod repo via gh| side-effect | `gh repo create space-rangers-mods-workshop/<mod> --public --source <out-dir> --push`; `mod/` packaged into `<mod>.zip` (ModuleInfo.txt at the archive root); `gh release create v2.0.0` with that archive |
| 8. Showcase — commit/push | side-effect | commit + push `mods.csv`, `README.md` and `profile/README.md` in `workshop/.github` (only after step 7 succeeds)                |

## Showcase — `.csv` → main page

`mods.csv` sits empty — header only:

```
mod_name,mod_author,mod_workshop_repo_name,mod_workshop_repo_link,mod_summary
```

Each run writes one row. The main page `README.md` is generated from this `.csv` (layout from
`template/showcase-readme.md`) and is never hand-edited. The `## ⚖️ License` section on the page is
the single public source of the CC BY-NC-SA 4.0 rationale.
