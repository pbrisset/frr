# Experiment 1 raw evidence

The frozen report, aggregate CSVs, and six plots are available directly in this
directory. The `trials/` paths referenced in the findings are inside
[experiment1_raw_archive.tar.gz](experiment1_raw_archive.tar.gz).

To verify or inspect the raw evidence outside the worktree, run these commands
from this directory.

```bash
scratch=$(mktemp -d)
tar -xzf experiment1_raw_archive.tar.gz -C "$scratch"
cp SHA256SUMS "$scratch/"
(cd "$scratch" && shasum -a 256 -c SHA256SUMS)
```

The manifest covers the trial logs, router configurations, packet captures,
metadata, truth, counts and timelines. Restore `trials/` alongside `analyze.py`
if you want to regenerate the aggregate tables and plots from the captures.
