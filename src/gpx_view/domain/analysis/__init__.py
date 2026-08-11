"""Track analysis: derived metrics, and the algorithms that produce them.

This package is the analysis authority. It reads the canonical normalized
geometry and nothing else -- no exchange format, no source application, no
importer -- so every adapter that reaches the normalization boundary reaches
these numbers unchanged.

```
NormalizedTrack geometry
        |
        v
   analyze_track            <- versioned by AnalysisProfile
        |
        v
    TrackAnalysis           <- rebuildable derived state, never source authority
```

What lives here:

* :mod:`~gpx_view.domain.analysis.profile` -- what produced a stored result,
* :mod:`~gpx_view.domain.analysis.metrics` -- the metric vocabulary and units,
* :mod:`~gpx_view.domain.analysis.distance` -- how far,
* :mod:`~gpx_view.domain.analysis.movement` -- how long, and how much of it moving,
* :mod:`~gpx_view.domain.analysis.elevation` -- how much up and down,
* :mod:`~gpx_view.domain.analysis.analyze` -- the one entry point.
"""

from gpx_view.domain.analysis.analyze import analyze_track
from gpx_view.domain.analysis.distance import (
    EARTH_RADIUS_METRES,
    geodesic_distance,
    segment_distance,
    track_distance,
)
from gpx_view.domain.analysis.elevation import ElevationAnalysis, analyse_elevation
from gpx_view.domain.analysis.metrics import (
    AnalysisQuality,
    MetricName,
    MetricUnit,
    MetricValue,
    TrackAnalysis,
    quality_in_canonical_order,
)
from gpx_view.domain.analysis.movement import (
    MOVEMENT_SPEED_THRESHOLD_MPS,
    MOVEMENT_WINDOW_SECONDS,
    MovementAnalysis,
    analyse_movement,
)
from gpx_view.domain.analysis.profile import ANALYSIS_PROFILE, AnalysisProfile

__all__ = [
    "ANALYSIS_PROFILE",
    "EARTH_RADIUS_METRES",
    "MOVEMENT_SPEED_THRESHOLD_MPS",
    "MOVEMENT_WINDOW_SECONDS",
    "AnalysisProfile",
    "AnalysisQuality",
    "ElevationAnalysis",
    "MetricName",
    "MetricUnit",
    "MetricValue",
    "MovementAnalysis",
    "TrackAnalysis",
    "analyse_elevation",
    "analyse_movement",
    "analyze_track",
    "geodesic_distance",
    "quality_in_canonical_order",
    "segment_distance",
    "track_distance",
]
