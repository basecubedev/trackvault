"""Executable contract for processing currency.

Reprocessing exists so that a parser or classifier upgrade can be applied to data
already held. That only works if the archive can answer one question:

```
was this generation produced by the processing this build installs?
```

The answer needs a single authority. Before this contract the versions involved
lived in three places -- importer and normalization schema on the processing run,
the classifier on each track's classification -- and a run that produced no track
recorded no classifier version at all. ``ProcessingProfile`` names the whole set
in one value, a run stores it, and ``is_processing_current`` is the only place
that compares.

The stored versions are data, not implementation detail: a run that claims the
version of an importer whose output it could not have produced makes every later
currency decision wrong. That is why this file pins them.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from gpx_view.application.processing import InstalledProcessing, installed_profile
from gpx_view.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    ProcessingProfile,
    ProcessingRun,
    ProcessingStatus,
    is_processing_current,
)
from gpx_view.domain.classifier import CLASSIFIER_METHOD, CLASSIFIER_VERSION
from gpx_view.infrastructure.gpx import GpxImporter

pytestmark = [pytest.mark.contract, pytest.mark.reprocessing]

SHA = "a" * 64
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

INSTALLED = ProcessingProfile(
    importer="gpx",
    importer_version="2",
    normalization_schema_version=2,
    classifier="evidence-weights",
    classifier_version="2",
)


def _run(profile: ProcessingProfile | None = None, **overrides: object) -> ProcessingRun:
    """Build one successful run that claims a processing profile."""
    used = profile or INSTALLED
    run = ProcessingRun(
        raw_import_sha256=SHA,
        importer=used.importer,
        importer_version=used.importer_version,
        normalization_schema_version=used.normalization_schema_version,
        processed_at=NOW,
        status=ProcessingStatus.SUCCEEDED,
        classifier=used.classifier,
        classifier_version=used.classifier_version,
    )
    return replace(run, **overrides) if overrides else run


# --- Semantically different output must not claim the same version -----------


def test_the_installed_importer_no_longer_claims_the_version_that_wrote_index_identities() -> None:
    """Importer version 1 produced output this build can no longer produce.

    Since version 1 was released the adapter gained a ``source_key`` candidate
    identity, moved evidence onto the candidate it was observed on, replaced
    ``source_link_present`` with a neutral external-link observation, started
    reading its root element structurally, and learned to tell a known extension
    schema from an element that merely shares its name. Every one of those
    changes the candidates, the identities or the evidence a document yields.

    A run that claims version 1 must therefore be distinguishable from one this
    build wrote, or `--outdated` cannot see the difference it exists to see.
    """
    assert GpxImporter().importer_version != "1"


def test_the_normalized_model_no_longer_claims_the_schema_version_without_identity() -> None:
    """Normalization schema 1 described candidates without a stable identity.

    Schema 1 normalized onto a track that was identified by its position in the
    source document, and its evidence came from the document rather than from the
    candidate. Data written under it is worth regenerating, which is exactly what
    a schema version is for.
    """
    assert NORMALIZATION_SCHEMA_VERSION != 1


def test_the_installed_profile_is_assembled_in_one_place() -> None:
    """One value names the whole processing, so no caller can assemble a second.

    Importer, normalization schema and classifier versions used to live in three
    unrelated places, and nothing could state which combination produced a stored
    generation.
    """
    profile = installed_profile(GpxImporter())

    assert profile.importer == GpxImporter().format_id
    assert profile.importer_version == GpxImporter().importer_version
    assert profile.normalization_schema_version == NORMALIZATION_SCHEMA_VERSION
    assert profile.classifier == CLASSIFIER_METHOD
    assert profile.classifier_version == CLASSIFIER_VERSION


# --- The currency authority --------------------------------------------------


def test_a_run_of_the_installed_profile_is_current() -> None:
    """The ordinary case: nothing changed, so nothing needs regenerating."""
    assert is_processing_current(_run(), INSTALLED)


@pytest.mark.parametrize(
    ("component", "older"),
    [
        ("importer_version", "1"),
        ("normalization_schema_version", 1),
        ("classifier_version", "1"),
    ],
)
def test_an_older_component_makes_a_generation_outdated(component: str, older: object) -> None:
    """Any part of the profile being older is enough. There is no ranking."""
    stale = replace(INSTALLED, **{component: older})

    assert not is_processing_current(_run(stale), INSTALLED)


def test_a_run_without_version_metadata_is_treated_as_outdated() -> None:
    """A run that cannot prove what produced it has not proved it is current.

    Runs written before the profile existed carry no classifier identity. Reading
    that absence as "probably fine" would silently exclude exactly the data the
    upgrade was meant to reach.
    """
    unproven = _run(classifier=None, classifier_version=None)

    assert unproven.profile is None
    assert not is_processing_current(unproven, INSTALLED)


def test_an_unknown_future_profile_is_not_current_either() -> None:
    """A generation this build cannot have produced is not this build's current one.

    Answering "current" would let an older deployment claim data written by a
    newer one is up to date, and then never regenerate it after a downgrade.
    """
    future = replace(INSTALLED, importer_version="99")

    assert not is_processing_current(_run(future), INSTALLED)


def test_a_failed_run_is_never_current() -> None:
    """A failed attempt produced no generation, so it can claim none."""
    failed = _run(status=ProcessingStatus.FAILED, error_code="invalid_gpx")

    assert not is_processing_current(failed, INSTALLED)


def test_a_source_that_was_never_processed_successfully_is_not_current() -> None:
    """No generation at all is the strongest form of out of date."""
    assert not is_processing_current(None, INSTALLED)


def test_a_run_of_an_importer_this_build_does_not_have_is_not_current() -> None:
    """An archive that lost an adapter cannot claim its data is up to date."""
    processing = InstalledProcessing((GpxImporter(),))
    foreign = _run(replace(INSTALLED, importer="fit"))

    assert processing.profile_for("fit") is None
    assert not processing.is_current(foreign)


def test_the_installed_processing_answers_for_the_importer_that_ran() -> None:
    """Currency compares like with like: one profile per installed adapter."""
    processing = InstalledProcessing((GpxImporter(),))

    assert processing.profile_for("gpx") == installed_profile(GpxImporter())
    assert processing.is_current(_run(installed_profile(GpxImporter())))
