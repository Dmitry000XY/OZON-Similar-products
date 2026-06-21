# Incremental daily artifact update

The pipeline supports two execution strategies:

```yaml
pipeline:
  update_strategy: full_retrain  # full_retrain | incremental
```

`full_retrain` is the default and remains the correctness fallback. It rebuilds
the train window daily artifacts, then runs the existing bucketed aggregation,
scoring, top-K, fallback, and output path.

`incremental` reuses valid daily artifacts when their manifests and fingerprints
match the current stage configuration. If an artifact is missing or invalid, the
current implementation rebuilds conservatively from the rolling window start.
That is intentionally less aggressive than partial replay, but it preserves
cross-midnight session correctness while still making repeat scoring/config runs
fast and stable when daily artifacts are valid.

## Daily Artifacts

The reusable daily layers are:

```text
data/processed/events_clean/date=YYYY-MM-DD.parquet
data/processed/session_state/active_sessions/date=YYYY-MM-DD.parquet
data/processed/session_state/max_session_indices/date=YYYY-MM-DD.parquet
data/processed/item_pairs/counts/date=YYYY-MM-DD.parquet
data/processed/item_pairs/user_keys/date=YYYY-MM-DD.parquet
data/processed/item_pairs/session_keys/date=YYYY-MM-DD.parquet
```

Each layer has a JSON sidecar manifest under `manifests/date=YYYY-MM-DD.json`.
Manifests record the artifact type, date, schema version, fingerprint, relative
paths, row counts, and metadata such as `processed_through_date` for daily pair
stats.

## Fingerprints

Artifacts are not reused just because files exist. Reuse requires a matching
manifest fingerprint and all required files.

Clean event fingerprints include the clean stage schema, date, and event action
types. Session state fingerprints include the clean fingerprint plus session
builder settings such as timeout, max items per session, and user bucket count.
Daily pair stat fingerprints include session state semantics, item-pair builder
settings, graph distance decay settings, and the processed-through date used to
build the artifact.

Scoring, top-K, fallback, and graph time decay settings do not invalidate daily
clean/session/pair artifacts. Time decay is applied during rolling aggregation,
and scoring-only changes are handled after daily pair reuse.

## Pair Stat Idempotency

Daily pair stats are written with a per-run idempotency rule:

```text
first write for date D in the current run:
  overwrite existing stale artifacts for D

second or later write for date D in the same run:
  merge with the already written artifact for D
```

This prevents repeated full retrains on the same self-hosted runner from
double-counting old pair stats, while preserving cross-midnight sessions that can
produce pair stats for an earlier session-start date later in the same run. In
those cases the artifact date / `pair_date` can be earlier than the
`processed_through_date` recorded in the manifest.

## Cutoff-Aware Pair Stat Reuse

Daily pair stats are cutoff-aware. A `date=D` artifact may be written while the
streaming session builder is processing a later day because cross-midnight
sessions can emit pairs whose `pair_date` is less than the current
`processed_through_date`. Reusing that artifact for a backfill ending at `D`
would leak future-day events into an earlier training cutoff.

To avoid that leakage, incremental reuse requires the daily pair stat
manifest's `metadata.processed_through_date` and fingerprint to match the
current window end. If the manifest is missing this metadata, or if the stored
processed-through date differs from the requested cutoff, the artifact is marked
invalid and the current implementation rebuilds conservatively from
`window_start`.

## Why Not Global Cleanup

The incremental path must not depend on deleting `data/processed`. Global cleanup
would hide stale-artifact bugs and remove the main benefit of daily reuse.
Instead, stale artifacts are overwritten locally when a date is rebuilt, and
valid artifacts are reused only through manifest validation.

## Why Not Delta Subtraction

Rolling pair aggregates are rebuilt by scanning the compact daily pair stats for
the requested window. The pipeline does not subtract expired aggregate days from
old aggregate tables because `unique_users` and `unique_sessions` cannot be
safely subtracted without key-level state.

## Session Boundaries

The streaming session builder carries active session tails across days. The
session state artifacts persist those active tails and per-user max session
indices after each processed day. This preserves the production-like daily
boundary model without re-enabling heavy `sessions_dir` materialization as the
default.

If any clean, session state, or pair stat artifact is invalid, the current
implementation rebuilds from `window_start`. Future work can use persisted state
to resume from the earliest safe affected date, but only if it can prove
cross-midnight pair stats for earlier session-start dates remain correct.

## Tuning

`pipeline.update_strategy` is config-only. It is not part of tuning search
spaces or objective selection.
