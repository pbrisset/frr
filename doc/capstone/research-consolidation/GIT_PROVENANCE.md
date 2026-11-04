# Git and source provenance

At consolidation start the branch was `dev/guna-experiments`, HEAD `f8fad0e9622bb2de8ee39316db0f431371010dc8`, matching its configured upstream and with no ordinary tracked, staged, or untracked changes. This does **not** mean all evidence was committed: `.gitignore` excludes `.pcapng`, many `.log` files and the historical ZIP. Many ignored captures/logs were present locally and are included in the consolidated ZIP. `ARTIFACT_MANIFEST.csv` records the actual status of every selected artifact at inventory time. The new consolidation directory and ZIP were created afterward.

Relevant commits, verified against their changed paths rather than subject alone:

| Commit | Scope and qualification |
|---|---|
| `689379cf25` | Phase 0 measurement harness, helper sources, topology/test changes and initial archived results. Phase 0 run manifest instead records earlier `727b0db7...` with dirty and untracked execution-time files; see its source snapshot and provenance hashes. |
| `709ca31664` | Phase 1A MBB harness, stimulus, smoke records and report. |
| `e5a21b4d93` | Phase 1B comparison harness and result aggregates/raw derived records. Matrix manifest records `709ca316...` and an untracked harness; current source copies are not cryptographic proof of the exact execution-time file. |
| `fd70931112` | Fleet evidence audit; AP report and raw study were not located. |
| `f8fad0e962` | Week 5 exploratory IP and causal verification documents, scripts and derived records. Many raw `.pcapng` and `.log` files remained locally ignored and were not included by ordinary staging. |

The historical Phase 1 review ZIP is ignored/untracked. SHA-256 `99d60192a2d47a74e72ab1f3a7dfe98189da6957cb330fe3ec6d07a2f09e082a`, 3,097 file members: 3,095 have byte-identical counterparts in selected sources/results. Its only two unique members—`MANIFEST.json` and `audit-calculations.py`—are preserved under `historical-review-unique/`. The original ZIP is not nested in the new ZIP, preventing duplicate inclusion; it remains untouched at `doc/capstone/review-packages/phase1-complete-review-package.zip`. Hash identity does not prove execution-time identity.

Recorded runtime was a privileged Linux aarch64 `frr-dev` container in Colima on a macOS host, kernel `6.8.0-117-generic`, FRR `10.8.0-dev-my-manual-build`. Recorded SHA-256: Zebra `d70f102f9171f006740c63b97f6e918d58aa8f197bd144dfa4dfda12539ddd5e`; bgpd `cb80f72969655250706292cada52e5c27db9e6f68ad6d1d7b81f47d232c056a0`. These hashes identify binaries, not exact binary-to-source correspondence. Per-run manifests, source hashes, command observations and clock notes are included.
