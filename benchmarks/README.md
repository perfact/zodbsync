# Benchmark Recipes

Use these presets when measuring playback changes. The goal is to compare the
same workload before and after each optimization.

## Presets

### Smoke

Fast sanity check for local iteration.

- `depth=3`
- `breadth=3`
- `blobs-per-folder=3`
- `blob-size=1024`
- `runs=3`

Command:

```sh
tox -e benchmark -- \
  --depth 3 \
  --breadth 3 \
  --blobs-per-folder 3 \
  --blob-size 1024 \
  --runs 3 \
  --output benchmarks/results/smoke.json
```

This produces a tree with 39 folders and 117 page templates.

### Playback

Primary comparison workload for playback optimizations.

- `depth=4`
- `breadth=5`
- `blobs-per-folder=5`
- `blob-size=4096`
- `runs=5`

Command:

```sh
tox -e benchmark -- \
  --depth 4 \
  --breadth 5 \
  --blobs-per-folder 5 \
  --blob-size 4096 \
  --runs 5 \
  --output benchmarks/results/playback.json
```

This produces a tree with 780 folders and 3900 page templates.

### Python Script Playback

Alternative workload to isolate non-template playback costs.

- `depth=4`
- `breadth=5`
- `blobs-per-folder=5`
- `blob-size=4096`
- `object-type=python_script`
- `runs=5`

Command:

```sh
tox -e benchmark -- \
  --depth 4 \
  --breadth 5 \
  --blobs-per-folder 5 \
  --blob-size 4096 \
  --object-type python_script \
  --runs 5 \
  --output benchmarks/results/python-script-playback.json
```

### Folder-Heavy Playback

Structural workload to isolate generic playback overhead with deep folder
creation and no leaf objects.

- `depth=5`
- `breadth=6`
- `blobs-per-folder=0`
- `object-type=folders`
- `runs=5`

Command:

```sh
tox -e benchmark -- \
  --depth 5 \
  --breadth 6 \
  --blobs-per-folder 0 \
  --object-type folders \
  --runs 5 \
  --output benchmarks/results/folder-heavy-playback.json
```

This produces a tree with 9330 folders and no page templates or Python
scripts.

## Workflow

1. Run the `Playback` preset on the current branch and keep the JSON output.
2. Implement one optimization at a time.
3. Run the same preset again.
4. Compare `playback_avg_seconds`, `playback_min_seconds`, and
   `playback_max_seconds`.

Include the current git revision in any captured result:

```sh
git rev-parse HEAD
```

The benchmark output already stores the repository `git_head`, so the JSON file
is the canonical artifact.

## Notes

The benchmark tox environment exists to ensure that Zope, `zope.mkzeoinstance`,
and the project itself are installed together and executed with the correct
`PATH`. Use it instead of invoking the module manually.

To inspect hot paths, enable profiling output:

```sh
tox -e benchmark -- \
  --depth 4 \
  --breadth 5 \
  --blobs-per-folder 5 \
  --blob-size 4096 \
  --object-type mixed \
  --runs 1 \
  --profile-dir benchmarks/results/profile \
  --output benchmarks/results/profiled-playback.json
```

This writes:

- `benchmarks/results/profile/playback-run-1.prof`
- `benchmarks/results/profile/playback-run-1.txt`

Use `--object-type page_template`, `python_script`, `mixed`, or `folders` to
compare how playback behaves for different object mixes and traversal-heavy
trees.

The JSON output also includes `playback_fs_cache_stats` so cache hit rates can
be compared before and after filesystem lookup changes.
