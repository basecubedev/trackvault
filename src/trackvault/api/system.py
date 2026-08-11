"""What this deployment is, for whoever has to reason about its numbers.

Distinct from `/healthz`, which answers whether the process can serve requests
and nothing else. This answers *which* TrackVault is serving them:

```
version              the release, from the one authority for it
schema_version       what the database is expected to be at
timezone             the zone every month and year boundary is drawn in
processing profiles  what an import turns a source into today
analysis profile     which algorithms produced the numbers on screen
```

The last two are the reason this exists rather than a version string in a
footer. "Why did my elevation gain change?" is answerable from the data when a
deployment can state which algorithms it runs, and unanswerable when it cannot.

Nothing here is configuration a reader could act on and nothing here is a
secret: no path, no data directory, no host, no style URL. It describes the
build, not the machine.
"""

from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from trackvault.application.system import GetSystemInfo
from trackvault.domain import ProcessingProfile
from trackvault.domain.analysis import AnalysisProfile

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class ProcessingProfileResponse(BaseModel):
    """What one installed adapter turns a source into today."""

    importer: str
    importer_version: str
    normalization_schema_version: int
    classifier: str
    classifier_version: str


class AnalysisProfileResponse(BaseModel):
    """The algorithms this build derives metrics with."""

    distance_algorithm: str
    distance_algorithm_version: int
    movement_algorithm: str
    movement_algorithm_version: int
    elevation_algorithm: str
    elevation_algorithm_version: int
    metric_schema_version: int


class SystemInfoResponse(BaseModel):
    """What this deployment is running."""

    version: str = Field(description="The TrackVault release, from its distribution metadata")
    schema_version: int = Field(description="Database schema version this build expects")
    timezone: str = Field(description="Zone every month and year boundary is drawn in")
    processing: list[ProcessingProfileResponse] = Field(
        description="One entry per installed import adapter"
    )
    analysis: AnalysisProfileResponse
    upload_enabled: bool = Field(
        description="Whether this deployment accepts files over HTTP. A page that "
        "offered the control anyway would be a page that lies about what the "
        "server will do."
    )


def _profile(profile: ProcessingProfile) -> ProcessingProfileResponse:
    """Shape one processing profile for HTTP."""
    return ProcessingProfileResponse(
        importer=profile.importer,
        importer_version=profile.importer_version,
        normalization_schema_version=profile.normalization_schema_version,
        classifier=profile.classifier,
        classifier_version=profile.classifier_version,
    )


def _analysis(profile: AnalysisProfile) -> AnalysisProfileResponse:
    """Shape the analysis profile for HTTP."""
    return AnalysisProfileResponse(
        distance_algorithm=profile.distance_algorithm,
        distance_algorithm_version=profile.distance_algorithm_version,
        movement_algorithm=profile.movement_algorithm,
        movement_algorithm_version=profile.movement_algorithm_version,
        elevation_algorithm=profile.elevation_algorithm,
        elevation_algorithm_version=profile.elevation_algorithm_version,
        metric_schema_version=profile.metric_schema_version,
    )


@router.get("/info", summary="What this deployment is running")
def read_system_info(request: Request) -> SystemInfoResponse:
    """Return the release, the schema and the algorithms this build applies.

    The version comes from the installed distribution metadata, so it is the
    same string ``pyproject.toml`` declares and the image label carries. A
    second constant here would be the one nobody bumps.
    """
    info = cast(GetSystemInfo, request.app.state.system_info)()
    return SystemInfoResponse(
        version=info.version,
        schema_version=info.schema_version,
        timezone=info.timezone,
        processing=[_profile(profile) for profile in info.processing],
        analysis=_analysis(info.analysis),
        upload_enabled=bool(request.app.state.upload_enabled),
    )
