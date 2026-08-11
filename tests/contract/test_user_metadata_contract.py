"""User-owned track metadata: a correction that never touches the source.

A personal archive stops being pleasant to keep the moment three tracks are
called `Track`. Correcting that is the user's own data, and the contract is
about where it lives rather than about what it says:

```
raw import          byte-identical, always            never written here
normalized title    what the document said            never overwritten
title override      what the user said                the display authority
```

Which is the same arrangement the classification override already has, and for
the same reason: a reprocess replaces the middle row entirely, so a correction
stored *in* it would be a correction a parser upgrade silently discards.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.application.import_tracks import ImportRequest
from trackvault.config import Settings
from trackvault.domain import (
    MAX_NOTE_LENGTH,
    MAX_TITLE_LENGTH,
    UserTrackMetadata,
    effective_title,
    normalize_note,
    normalize_title,
)
from trackvault.infrastructure.assembly import build_services
from trackvault.main import create_app

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
RECORDING = "recorded-measurements.gpx"
SOURCE_TITLE = "Synthetic morning walk"


@pytest.fixture
def archive(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client whose archive holds one recorded track."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    services.import_tracks(
        ImportRequest(content=(FIXTURES / RECORDING).read_bytes(), original_filename=RECORDING)
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _track_id(client: TestClient) -> int:
    """Return the identity of the one track in the archive."""
    return int(client.get("/api/v1/tracks").json()["tracks"][0]["id"])


# --- The domain rules -------------------------------------------------------


@pytest.mark.unit
def test_a_title_falls_back_to_the_source_when_the_user_states_none() -> None:
    """One authority for what a track is called, projected from two values."""
    assert effective_title("From the file", UserTrackMetadata()) == "From the file"
    assert effective_title("From the file", UserTrackMetadata(title="Mine")) == "Mine"
    assert effective_title(None, UserTrackMetadata()) is None


@pytest.mark.unit
def test_a_blank_title_is_absence_rather_than_an_empty_name() -> None:
    """Typing spaces is how somebody clears a correction.

    Storing `"  "` would store a title that renders as nothing and then hides
    the source title behind it -- a correction that makes the track *less*
    identifiable and cannot be told apart from one somebody meant.
    """
    assert normalize_title("   ") is None
    assert normalize_title("") is None
    assert normalize_title(None) is None
    assert normalize_title("  Evening loop  ") == "Evening loop"


@pytest.mark.unit
def test_text_is_bounded_and_stays_a_single_line() -> None:
    """Plain text, bounded. An unauthenticated text field is a growth decision."""
    with pytest.raises(ValueError, match="at most"):
        normalize_title("x" * (MAX_TITLE_LENGTH + 1))
    with pytest.raises(ValueError, match="single line"):
        normalize_title("two\nlines")
    with pytest.raises(ValueError, match="at most"):
        normalize_note("x" * (MAX_NOTE_LENGTH + 1))

    assert normalize_note("first\n\nsecond") == "first\n\nsecond"


@pytest.mark.unit
def test_stored_metadata_must_already_be_normalized() -> None:
    """No path may write a value the validation rules would have refused."""
    with pytest.raises(ValueError, match="normalized"):
        UserTrackMetadata(title="  padded  ")

    assert UserTrackMetadata().is_empty
    assert not UserTrackMetadata(note="something").is_empty


# --- The HTTP contract ------------------------------------------------------


def test_a_track_reports_the_source_title_until_somebody_corrects_it(
    archive: TestClient,
) -> None:
    """Both halves are visible, so a correction is legible as one."""
    track = archive.get("/api/v1/tracks").json()["tracks"][0]

    assert track["title"] == SOURCE_TITLE
    assert track["metadata"] == {
        "title": None,
        "note": None,
        "source_title": SOURCE_TITLE,
        "is_overridden": False,
    }


def test_a_correction_becomes_the_displayed_title_without_replacing_the_source(
    archive: TestClient,
) -> None:
    """The document keeps saying what it said. Only the display changes."""
    track_id = _track_id(archive)

    response = archive.patch(
        f"/api/v1/tracks/{track_id}/metadata", json={"title": "Sunday around the lake"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Sunday around the lake"
    assert body["metadata"]["source_title"] == SOURCE_TITLE
    assert body["metadata"]["is_overridden"] is True
    assert archive.get(f"/api/v1/tracks/{track_id}").json()["title"] == "Sunday around the lake"


def test_a_correction_shows_in_the_listing_as_well_as_the_detail(archive: TestClient) -> None:
    """One authority, every read surface. A row and a page cannot disagree."""
    track_id = _track_id(archive)
    archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": "Renamed"})

    listed = archive.get("/api/v1/tracks").json()["tracks"][0]

    assert listed["title"] == "Renamed"
    assert listed["metadata"]["source_title"] == SOURCE_TITLE


def test_clearing_the_title_hands_the_display_back_to_the_source(archive: TestClient) -> None:
    """Reset is a real operation, not a rename to something empty."""
    track_id = _track_id(archive)
    archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": "Renamed"})

    reset = archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": None})

    assert reset.json()["title"] == SOURCE_TITLE
    assert reset.json()["metadata"]["title"] is None
    assert reset.json()["metadata"]["is_overridden"] is False


def test_a_partial_update_leaves_the_field_it_did_not_mention_alone(
    archive: TestClient,
) -> None:
    """The failure this exists against: renaming a track deleting its note."""
    track_id = _track_id(archive)
    archive.patch(
        f"/api/v1/tracks/{track_id}/metadata",
        json={"title": "Renamed", "note": "the receiver lost its fix in the tunnel"},
    )

    renamed = archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": "Again"})

    assert renamed.json()["metadata"]["note"] == "the receiver lost its fix in the tunnel"
    assert renamed.json()["metadata"]["title"] == "Again"


def test_a_note_can_be_removed_without_touching_the_title(archive: TestClient) -> None:
    """Both fields are independently clearable."""
    track_id = _track_id(archive)
    archive.patch(
        f"/api/v1/tracks/{track_id}/metadata", json={"title": "Renamed", "note": "something"}
    )

    cleared = archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"note": None})

    assert cleared.json()["metadata"]["note"] is None
    assert cleared.json()["title"] == "Renamed"


def test_an_over_long_title_is_refused_rather_than_truncated(archive: TestClient) -> None:
    """Somebody's sentence is not this application's to shorten."""
    track_id = _track_id(archive)

    response = archive.patch(
        f"/api/v1/tracks/{track_id}/metadata", json={"title": "x" * (MAX_TITLE_LENGTH + 1)}
    )

    assert response.status_code == 422
    assert "traceback" not in response.text.lower()


def test_metadata_of_an_unknown_track_answers_the_stable_error(archive: TestClient) -> None:
    """The same envelope every other absent track produces."""
    response = archive.patch("/api/v1/tracks/999999/metadata", json={"title": "Nope"})

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "track_not_found", "message": "no such track"}}


def test_metadata_is_stored_as_plain_text_and_never_as_markup(archive: TestClient) -> None:
    """Nothing here is an HTML authority.

    The archive stores exactly what was typed, including characters that would
    be markup somewhere else, and hands it back unchanged. Escaping is the
    renderer's job and doing it here would corrupt the stored value instead.
    """
    track_id = _track_id(archive)
    typed = "Ride <b>along</b> the & river"

    response = archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": typed})

    assert response.json()["metadata"]["title"] == typed


# --- Reprocessing, and a candidate that comes back --------------------------


@pytest.mark.reprocessing
@pytest.mark.persistence
def test_a_correction_survives_reprocessing_and_the_source_stays_byte_identical(
    tmp_path: Path,
) -> None:
    """The whole reason this is a table of its own.

    Reprocessing replaces the normalized projection and the detected
    classification of the same candidate. A title stored in that projection
    would be a title an importer upgrade silently discards, and nobody would
    know which of their corrections were gone.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(content=(FIXTURES / RECORDING).read_bytes(), original_filename=RECORDING)
    )
    sha256 = outcome.sha256
    assert sha256 is not None
    track_id = services.store.track_ids_for(sha256)[0]
    with TestClient(create_app(settings)) as client:
        client.patch(
            f"/api/v1/tracks/{track_id}/metadata",
            json={"title": "Mine", "note": "and my note"},
        )

    artifact = next(settings.raw_storage_dir.rglob("*.raw"))
    before = artifact.read_bytes()
    services.reprocess(sha256)
    after = services.store.get_track(track_id)

    assert after is not None
    assert after.user_metadata == UserTrackMetadata(title="Mine", note="and my note")
    assert after.display_title == "Mine"
    assert after.title == SOURCE_TITLE
    assert artifact.read_bytes() == before
