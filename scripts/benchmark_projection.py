"""Measure what a large track costs to read, stage by stage.

A developer tool, deliberately not a test. Wall-clock numbers on a shared CI
runner are noise with a threshold attached, and a suite that fails because
somebody else's build was compiling is a suite people learn to re-run rather
than to read. What *is* a test is that the responses stay bounded whatever the
input -- ``tests/integration/test_large_track_projection.py``.

Run it before optimising anything:

```bash
uv run python scripts/benchmark_projection.py
uv run python scripts/benchmark_projection.py --sizes 1000 100000 --repeat 3
```

The stages are measured separately because "the profile takes 2.4 seconds" is
not a finding, it is the absence of one. Adding a cache to a cost nobody has
attributed is how a stale-data lifecycle gets introduced to fix an inefficient
loop.

The geometry is synthetic and deterministic: a walked path with a plausible
sampling interval, receiver noise from a fixed seed, and terrain that actually
goes up and down, so the elevation filter and the movement window have something
to do. No private data, and the same numbers on every machine.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from gpx_view.application.projection import decimate_profile, simplify_geometry
from gpx_view.application.track_profile import DEFAULT_PROFILE_SAMPLES
from gpx_view.domain.analysis.elevation import filtered_elevation_profile
from gpx_view.domain.analysis.movement import sustained_speeds
from gpx_view.domain.analysis.series import derive_profile
from gpx_view.domain.geometry import TrackPoint, TrackSegment

DEFAULT_SIZES = (1_000, 10_000, 50_000, 100_000, 250_000)
SAMPLING_SECONDS = 2
SEED = 20260809


def synthetic_track(points: int, *, segments: int = 3) -> tuple[TrackSegment, ...]:
    """Return a deterministic walked track of roughly ``points`` positions.

    Shaped so every stage has real work: the path advances, wanders within a
    receiver's envelope, and climbs and descends a hill twice.
    """
    generator = random.Random(SEED)  # noqa: S311 - deterministic fixture, not security
    per_segment = max(2, points // segments)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    built: list[TrackSegment] = []
    index = 0

    for segment in range(segments):
        positions: list[TrackPoint] = []
        for _step in range(per_segment):
            progress = index / max(1, points)
            positions.append(
                TrackPoint(
                    latitude=52.5 + progress * 0.05 + generator.uniform(-3e-5, 3e-5),
                    longitude=13.4
                    + math.sin(progress * 12.0) * 0.02
                    + generator.uniform(-3e-5, 3e-5),
                    elevation=40.0 + math.sin(progress * 6.0) * 60.0 + generator.uniform(-1.5, 1.5),
                    time=start + timedelta(seconds=(index * SAMPLING_SECONDS) + segment * 600),
                )
            )
            index += 1
        built.append(TrackSegment(points=tuple(positions)))
    return tuple(built)


@dataclass(frozen=True, slots=True)
class Stage:
    """One measurable step of turning geometry into a response."""

    name: str
    run: Callable[[tuple[TrackSegment, ...]], object]


def _elevation(segments: tuple[TrackSegment, ...]) -> object:
    """Filter every segment's altitude, as the profile and the ascent both do."""
    return [filtered_elevation_profile(segment) for segment in segments]


def _payload(segments: tuple[TrackSegment, ...]) -> object:
    """Serialise the bounded response a client actually receives.

    Measured separately because it is the one stage whose cost does *not* grow
    with the track: the reduction happens first, so a 250 000-position recording
    and a 10 000-position one serialise the same few thousand samples.
    """
    reduced = decimate_profile(derive_profile(segments), DEFAULT_PROFILE_SAMPLES)
    return json.dumps(
        [
            [
                {
                    "segment_index": sample.segment_index,
                    "point_index": sample.point_index,
                    "distance_m": sample.cumulative_distance_m,
                    "latitude": sample.latitude,
                    "longitude": sample.longitude,
                    "elevation_m": sample.raw_elevation_m,
                    "filtered_elevation_m": sample.filtered_elevation_m,
                    "speed_mps": sample.sustained_speed_mps,
                }
                for sample in segment.samples
            ]
            for segment in reduced.segments
        ]
    )


def _stages() -> tuple[Stage, ...]:
    """Return the stages a `/profile` and a `/geometry` request pay for."""
    return (
        Stage("elevation filter", _elevation),
        Stage("movement window", sustained_speeds),
        Stage("profile series", derive_profile),
        Stage("profile decimation", lambda s: decimate_profile(derive_profile(s), 3_000)),
        Stage("map simplification", lambda s: simplify_geometry(s, 5_000)),
        Stage("bounded payload", _payload),
    )


def _measure(stage: Stage, segments: tuple[TrackSegment, ...], repeat: int) -> float:
    """Return the fastest of ``repeat`` runs, in seconds.

    The fastest rather than the mean: a slower run measured this machine's other
    work, and the question here is what the code costs.
    """
    best = math.inf
    for _ in range(repeat):
        started = time.perf_counter()
        stage.run(segments)
        best = min(best, time.perf_counter() - started)
    return best


def main(argv: Sequence[str] | None = None) -> int:
    """Measure every stage at every requested size and print a table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--repeat", type=int, default=3)
    arguments = parser.parse_args(argv)

    stages = _stages()
    header = f"{'points':>9}  " + "  ".join(f"{stage.name:>19}" for stage in stages)
    sys.stdout.write(f"{header}\n{'-' * len(header)}\n")

    for size in arguments.sizes:
        segments = synthetic_track(size)
        actual = sum(len(segment.points) for segment in segments)
        timings = [_measure(stage, segments, arguments.repeat) for stage in stages]
        row = "  ".join(f"{timing * 1000:>16.1f} ms" for timing in timings)
        sys.stdout.write(f"{actual:>9}  {row}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
