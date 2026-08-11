"""What this build is, as one value.

Not diagnostics and not configuration: the three facts that make a number on a
screen explainable afterwards.

```
version              which release produced this page
processing profiles  what an import turns a source into today
analysis profile     which algorithms derived the metrics
```

The last two already exist as authorities -- ``InstalledProcessing`` and
``InstalledAnalysis`` -- and this reads them rather than restating them. A
second list of installed versions would be the one nobody updates, and it would
be the one an operator is looking at while trying to work out why a figure
changed.
"""

from dataclasses import dataclass

from gpx_view.application.analysis import InstalledAnalysis
from gpx_view.application.processing import InstalledProcessing
from gpx_view.domain import ProcessingProfile
from gpx_view.domain.analysis import AnalysisProfile


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """What one deployment is running.

    Attributes:
        version: The GPX-View release.
        schema_version: The database schema version this build expects.
        timezone: The zone every month and year boundary is drawn in.
        processing: One profile per installed import adapter, ordered by name.
        analysis: The analysis profile this build derives with.
    """

    version: str
    schema_version: int
    timezone: str
    processing: tuple[ProcessingProfile, ...]
    analysis: AnalysisProfile


class GetSystemInfo:
    """Answers what this build is running."""

    def __init__(
        self,
        *,
        version: str,
        schema_version: int,
        timezone: str,
        processing: InstalledProcessing,
        analysis: InstalledAnalysis,
    ) -> None:
        """Wire the query to the version, the schema and the two authorities."""
        self._version = version
        self._schema_version = schema_version
        self._timezone = timezone
        self._processing = processing
        self._analysis = analysis

    def __call__(self) -> SystemInfo:
        """Return what this deployment is running, right now."""
        return SystemInfo(
            version=self._version,
            schema_version=self._schema_version,
            timezone=self._timezone,
            processing=self._processing.profiles,
            analysis=self._analysis.profile,
        )


__all__ = ["GetSystemInfo", "SystemInfo"]
