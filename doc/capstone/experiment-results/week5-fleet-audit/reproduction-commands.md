# Reproduction and provenance

Run from the repository root. Searches are read-only; paths outside the checkout may require access. Search results reflect only locally available refs and files, not remote repositories. `git status` after creation will show this audit as untracked.

```sh
git status --short --branch
git rev-parse HEAD
git log --all --oneline -- '*mobility_scale*' '*mobility_prober*' '*run_mobility_points*' '*analyze_mobility*'
rg --files | rg 'mobility|scale|audit|phase'
find /Users/gunabhiram/College -iname '*mobility*scale*' -o -name mobility_prober.py -o -name run_mobility_points.sh -o -name analyze_mobility.py
shasum -a 256 doc/capstone/experiment-results/phase0-2026-11-02/verified-move/manifest.json
```

Original Phase 0 and Phase 1 commands are in their reports; they require the privileged Linux `frr-dev` container, unique output directories and sequential topology runs. They reproduce the **separate** single-robot tests, not the AP fleet study. For AP reproduction no honest executable command can be given until the original harness, settings and raw dataset are supplied. Hash the recovered sources, binaries, configuration and all run outputs; store manifest, logs, captures and analysis under a new run directory. Do not overwrite existing artifacts.

Recommended commit only after review and with the contributor's real configured identity:

```sh
git add doc/capstone/experiment-results/week5-fleet-audit
git diff --cached --check
git commit -s -m 'doc: audit fleet mobility evidence gaps'
```

No commit, push, reset or clean was performed.
