# Week 5 IP relearning: causal verification

**Decision: CAUSAL MECHANISM PARTIALLY SUPPORTED.** Twenty matched BBM trials and a separate access-port check support a timing-sensitive `NOARP`/neighbor-learning sequence, but they do not trace the running kernel's update return or Zebra's internal decision. The result is not a demonstrated FRR defect.

Start with [causal verification report](causal-verification-report.md), [hypothesis status](hypothesis-status.csv), [kernel and FRR source analysis](source-analysis.md), and [limitations](limitations.md). The requested event, ARP, BGP, forwarding, packet-path and trial CSVs index raw records in `raw-*`. Each run includes captures, packet/drop logs, netlink and router logs, events, probes, and separate-clock timelines. Paths in CSVs are relative to this directory. See [reproduction commands](reproduction-commands.md) and `SHA256SUMS` for provenance.

The earlier staged `week5-ip-relearning` work was preserved. No FRR core source or production configuration was changed, and no commit or push was made.
