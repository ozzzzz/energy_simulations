"""Downsampling that keeps the findings.

A run can be 14,000 ticks; a chart needs a couple of thousand points. Three of
the rules below are correctness, not cosmetics:

* rate series are averaged **weighted by dt**, which is the only aggregation that
  preserves energy across a non-uniform timebase;
* state series take the last (or most common) value — averaging a state code is
  meaningless;
* series where the *peak is the point* ship a companion max/min, because a
  60 second 105 % UPS overload averaged into a five-minute bucket disappears —
  and that is exactly the finding its scenario exists to show.

Event windows are additionally kept at fine resolution, so the interesting
minutes survive at close to full detail while a quiet week collapses.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from app.simulations.sim1.telemetry import SeriesSpec


@dataclass(frozen=True, slots=True)
class Buckets:
    groups: tuple[tuple[int, ...], ...]
    """Row-index groups, in time order. Every row belongs to exactly one group."""

    def __len__(self) -> int:
        return len(self.groups)


def plan_buckets(
    t: Sequence[float],
    dt: Sequence[float],
    target_points: int,
    windows: Sequence[tuple[float, float]] = (),
    window_step_s: float = 2.0,
) -> Buckets:
    """Group rows into at most roughly ``target_points`` buckets.

    Rows inside an event window are grouped only up to ``window_step_s`` of
    simulated time; everything else is grouped to whatever step spends the
    remaining budget evenly.
    """
    n = len(t)
    if n == 0:
        return Buckets(groups=())
    if target_points <= 0 or n <= target_points:
        return Buckets(groups=tuple((i,) for i in range(n)))

    def protected(time: float) -> bool:
        return any(low <= time < high for low, high in windows)

    protected_s = sum(float(dt[i]) for i in range(n) if protected(float(t[i])))
    total_s = sum(float(d) for d in dt)
    window_points = int(protected_s / window_step_s) if window_step_s > 0.0 else 0
    coarse_budget = max(1, target_points - window_points)
    coarse_step = max(1e-9, (total_s - protected_s) / coarse_budget)

    groups: list[tuple[int, ...]] = []
    current: list[int] = []
    accrued = 0.0
    current_protected = protected(float(t[0]))

    for i in range(n):
        row_protected = protected(float(t[i]))
        limit = window_step_s if row_protected else coarse_step
        if current and (row_protected != current_protected or accrued >= limit):
            groups.append(tuple(current))
            current = []
            accrued = 0.0
            current_protected = row_protected
        current.append(i)
        accrued += float(dt[i])

    if current:
        groups.append(tuple(current))
    return Buckets(groups=tuple(groups))


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float | None:
    present = values.dropna()
    if present.empty:
        return None
    aligned = weights.reindex(present.index)
    total = float(aligned.sum())
    if total <= 0.0:
        return float(present.iloc[-1])
    return float((present * aligned).sum() / total)


def aggregate_series(df: pd.DataFrame, spec: SeriesSpec, buckets: Buckets) -> list[float | None]:
    values = df[spec.column]
    weights = df["dt"]
    out: list[float | None] = []
    for group in buckets.groups:
        chunk = values.iloc[list(group)]
        if spec.agg == "last":
            tail = chunk.dropna()
            out.append(None if tail.empty else float(tail.iloc[-1]))
        elif spec.agg == "max":
            out.append(None if chunk.dropna().empty else float(chunk.max()))
        elif spec.agg == "min":
            out.append(None if chunk.dropna().empty else float(chunk.min()))
        else:
            out.append(_weighted_mean(chunk, weights.iloc[list(group)]))
    return out


def aggregate_extreme(df: pd.DataFrame, column: str, buckets: Buckets, which: str) -> list[float | None]:
    values = df[column]
    out: list[float | None] = []
    for group in buckets.groups:
        chunk = values.iloc[list(group)].dropna()
        if chunk.empty:
            out.append(None)
        else:
            out.append(float(chunk.max() if which == "max" else chunk.min()))
    return out


def aggregate_state(df: pd.DataFrame, column: str, buckets: Buckets) -> list[str]:
    values = df[column].astype(str)
    return [str(values.iloc[list(group)].iloc[-1]) for group in buckets.groups]


def bucket_times(df: pd.DataFrame, buckets: Buckets) -> tuple[list[float], list[float]]:
    """Bucket start times and total durations, so the x-axis stays honest."""
    times: list[float] = []
    spans: list[float] = []
    for group in buckets.groups:
        rows = df.iloc[list(group)]
        times.append(float(rows["t"].iloc[0]))
        spans.append(float(rows["dt"].sum()))
    return times, spans


def thin(df: pd.DataFrame, max_points: int, windows: Sequence[tuple[float, float]] = ()) -> pd.DataFrame:
    """Bucket a frame down for plotting, keeping the last row of each bucket.

    Used by the Plotly analysis figures, which need a frame rather than arrays.
    Peaks are preserved separately by the ``__max`` columns the payload ships;
    this is for the smooth traces.
    """
    if df.empty or len(df) <= max_points:
        return df
    buckets = plan_buckets(df["t"].tolist(), df["dt"].tolist(), max_points, windows)
    keep = [group[-1] for group in buckets.groups]
    return df.iloc[keep].reset_index(drop=True)
