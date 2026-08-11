"""What processing this build installs, and whether a stored generation matches.

A :class:`~gpx_view.domain.processing.ProcessingProfile` describes processing
that already happened. This module answers the other half: what the *current*
build would do with a source today, so the two can be compared.

The profile is assembled from the adapter that reads the format, the normalized
model's schema version and the installed classification rules. Assembling it in
one place is the point -- three constants read from three modules is how they
came to disagree, and a version nobody bumped is indistinguishable from one
nobody needed to bump.

There is one profile per installed importer, because currency compares like with
like: a GPX generation is out of date when the GPX adapter moved on, and a future
FIT adapter's version says nothing about it.
"""

from collections.abc import Sequence

from gpx_view.application.importing import TrackImporter
from gpx_view.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    ProcessingProfile,
    ProcessingRun,
    is_processing_current,
)
from gpx_view.domain.classifier import CLASSIFIER_METHOD, CLASSIFIER_VERSION


def installed_profile(importer: TrackImporter) -> ProcessingProfile:
    """Return the processing profile this build applies through one adapter."""
    return ProcessingProfile(
        importer=importer.format_id,
        importer_version=importer.importer_version,
        normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
        classifier=CLASSIFIER_METHOD,
        classifier_version=CLASSIFIER_VERSION,
    )


class InstalledProcessing:
    """The processing profiles this build offers, and the currency authority.

    Every question of the form "does this stored generation need regenerating?"
    is answered here, so `reprocess --outdated`, the diagnostics view and
    anything added later cannot answer it differently.
    """

    def __init__(self, importers: Sequence[TrackImporter]) -> None:
        """Derive one profile per installed adapter."""
        self._profiles = {importer.format_id: installed_profile(importer) for importer in importers}

    @property
    def profiles(self) -> tuple[ProcessingProfile, ...]:
        """Return every profile this build offers, ordered by importer name."""
        return tuple(self._profiles[name] for name in sorted(self._profiles))

    def profile_for(self, importer: str) -> ProcessingProfile | None:
        """Return the profile of one installed adapter, or ``None`` if it has none.

        An archive whose data was written by an adapter this build no longer
        ships gets ``None``, and therefore never counts as current: nothing here
        can vouch for output it cannot reproduce.
        """
        return self._profiles.get(importer)

    def is_current(self, run: ProcessingRun | None) -> bool:
        """Report whether a run's generation was produced by the installed processing."""
        if run is None:
            return False
        return is_processing_current(run, self.profile_for(run.importer))
