# Cisco capstone research consolidation through Week 5

This package organizes our own EVPN-VXLAN robot mobility evidence. It is a retrospective audit, not a new experiment and not a validation of the missing teammate AP study. The matrix contains 61 records: 57 recorded successful single-move executions (including smoke/pilots), two historical workload executions and two failed instrumentation attempts. Do not pool their conditions or count individual observations as independent trials.

- [Self-contained handoff](CAPSTONE_RESEARCH_HANDOFF.md) and [chronological research summary](MASTER_RESEARCH_SUMMARY.md).
- [Experiment matrix](EXPERIMENT_MATRIX.csv), [findings register](FINDINGS_REGISTER.csv), [claim/evidence map](CLAIM_EVIDENCE_MAP.csv), and [validation checks](VALIDATION_CHECKS.csv).
- [Protocol walkthrough](PROTOCOL_EVENT_WALKTHROUGH.md), [open questions](UNRESOLVED_QUESTIONS.md), and [teammate comparison checklist](TEAMMATE_COMPARISON_CHECKLIST.md).
- [Reproduction guide](REPRODUCTION_GUIDE.md), [Git provenance](GIT_PROVENANCE.md), and [artifact manifest](ARTIFACT_MANIFEST.csv).

Source evidence is retained at the original relative paths under `../experiment-results/` and `../../../tests/topotests/bgp_evpn_capstone/` in the archive. `ARTIFACT_MANIFEST.csv` inventories included artifacts, their original Git status and hash; its own content hash is covered by the ZIP's `ARCHIVE_CONTENT_SHA256SUMS`, avoiding a self-referential hash. The historical review ZIP is omitted as a duplicate; its two unique members and its own manifest are retained under `historical-review-unique/`. See provenance for the hash comparison and explicit exclusions.

The package is **PARTIALLY READY** for teammate comparison: our evidence can be reviewed, but the teammate's AP study and precise kernel/Zebra mechanism remain unavailable or unresolved. No production capacity, AP result, or FRR defect is established.
