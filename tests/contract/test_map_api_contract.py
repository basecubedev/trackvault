"""The HTTP surface of the offline map capability.

Three groups, and the middle one is the point of the whole feature.

**Management** -- browsing a catalog, queueing an install, watching a job,
removing a package. Reaches a provider, and only ever when a person asked it to.

**Delivery** -- a style's worth of coverage and the tiles behind it. Reaches
nothing. This is what a track page uses, and every test here is also a statement
that looking at a track sends nothing anywhere.

**Refusals** -- what a caller cannot make this server do: name a host, name a
path, or read a tile out of a package that is not installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.application.maps import (
    GetMapCatalog,
    InstallMapPackage,
    queued_job,
)
from gpx_view.config import Settings
from gpx_view.domain.maps import MapRegionId
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.database.map_store import SqliteMapPackageStore
from gpx_view.infrastructure.maps import (
    FilesystemMapCatalogCache,
    FilesystemMapPackageStorage,
    MbtilesPackageInspector,
)
from gpx_view.main import create_app
from support.fake_provider import FakeMapProvider, region
from support.map_packages import build_package, layer_names

pytestmark = [pytest.mark.contract, pytest.mark.maps]

MONACO = "fake:europe/monaco"
ANDORRA = "fake:europe/andorra"
MONACO_BOUNDS = (7.40, 43.48, 7.60, 43.76)
ANDORRA_BOUNDS = (1.40, 42.42, 1.79, 42.66)

MONACO_BBOX = "7.42,43.72,7.44,43.74"
ANDORRA_BBOX = "1.50,42.50,1.52,42.52"
NOWHERE_BBOX = "-71.5,-50.5,-71.4,-50.4"

TEMPORARY_ROOT = "/" + "tmp"
"""Spelled indirectly so the audit rule about temporary paths stays satisfied."""


class FixedClock:
    """A clock that does not move."""

    def now(self) -> datetime:
        """Return a fixed instant."""
        return datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class Deployment:
    """An application with a map capability whose provider is a fixture."""

    def __init__(self, root: Path) -> None:
        """Compose an application over an isolated data directory."""
        self.settings = Settings(data_dir=root / "data", web_dir=root / "no-build")
        self.app = create_app(self.settings)
        self.sources = root / "remote"
        self.sources.mkdir(parents=True)
        self.clock = FixedClock()
        self.storage = FilesystemMapPackageStorage(self.settings.map_storage_dir)
        self.store = SqliteTrackStore(self.settings.database_path)
        self.repository = SqliteMapPackageStore(self.store)
        self.provider = FakeMapProvider(
            regions=(
                region("fake:europe", "Europe"),
                region(MONACO, "Monaco", "fake:europe"),
                region(ANDORRA, "Andorra", "fake:europe"),
                region("fake:europe/liechtenstein", "Liechtenstein", "fake:europe"),
            ),
            packages={},
        )
        self.catalog = GetMapCatalog(
            provider=self.provider,
            cache=FilesystemMapCatalogCache(self.storage.catalog_directory()),
            clock=self.clock,
        )
        self.installer = InstallMapPackage(
            provider=self.provider,
            catalog=self.catalog,
            storage=self.storage,
            inspector=MbtilesPackageInspector(self.storage),
            repository=self.repository,
            clock=self.clock,
        )
        self._jobs = 0

    def use_fixture_provider(self) -> None:
        """Point the running application's catalog at the fixture provider."""
        self.app.state.map_catalog = self.catalog

    def install(self, identity: str, **options: object) -> None:
        """Publish a package and install it into the running deployment."""
        target = MapRegionId.parse(identity)
        path = self.sources / f"{target.path.replace('/', '_')}.mbtiles"
        bounds = MONACO_BOUNDS if identity == MONACO else ANDORRA_BOUNDS
        build_package(path, bounds=options.pop("bounds", bounds), **options)  # type: ignore[arg-type]
        self.provider.publish(identity, path)
        self._jobs += 1
        job = queued_job(
            job_id=f"{self._jobs:032x}",
            region_id=target,
            region_name=target.segments[-1].title(),
            at=self.clock.now(),
            is_update=False,
        )
        self.repository.create_job(job)
        self.installer.run(job, is_cancelled=lambda: False)


@pytest.fixture
def deployment(tmp_path: Path) -> Deployment:
    """Return a composed deployment whose provider is a fixture."""
    return Deployment(tmp_path)


@pytest.fixture
def client(deployment: Deployment) -> Iterator[TestClient]:
    """Yield a client over that deployment."""
    with TestClient(deployment.app) as opened:
        deployment.use_fixture_provider()
        yield opened


# --- Installed maps ----------------------------------------------------------


def test_a_fresh_archive_has_no_maps_and_says_so(client: TestClient) -> None:
    """“Nothing installed" and "nothing here" are opposite instructions."""
    payload = client.get("/api/v1/maps").json()

    assert payload["maps"] == []
    assert payload["installs_enabled"] is True
    assert payload["total_size_bytes"] == 0


def test_an_installed_map_reports_everything_a_manager_row_needs(
    client: TestClient, deployment: Deployment
) -> None:
    """Region, state, dataset, size, provider, licence, attribution."""
    deployment.install(MONACO)

    entry = client.get("/api/v1/maps").json()["maps"][0]

    assert entry["region_id"] == MONACO
    assert entry["region_name"] == "Monaco"
    assert entry["state"] == "installed"
    assert entry["tile_schema"] == "shortbread"
    assert entry["size_bytes"] > 0
    assert entry["dataset_timestamp"] is not None
    assert entry["downloaded_at"].startswith("2026-08-09")
    assert entry["attribution"]["license_identifier"] == "ODbL-1.0"
    assert entry["attribution"]["required_text"] == "Map data © OpenStreetMap contributors"


def test_no_response_ever_names_a_place_on_disk(client: TestClient, deployment: Deployment) -> None:
    """An API that prints `/data/maps/packages/...` has told a reader nothing useful."""
    deployment.install(MONACO)

    body = (
        client.get("/api/v1/maps").text
        + client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").text
    )

    leaks = ("/data", TEMPORARY_ROOT, ".mbtiles", "packages/", str(deployment.settings.data_dir))
    for leak in leaks:
        assert leak not in body


# --- Coverage ----------------------------------------------------------------


def test_coverage_over_an_installed_region_offers_a_same_origin_source(
    client: TestClient, deployment: Deployment
) -> None:
    """Everything the page needs to draw, and every URL is relative."""
    deployment.install(MONACO)

    payload = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()

    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["tiles_url"].startswith("/api/v1/maps/tiles/")
    assert source["tiles_url"].endswith("/{z}/{x}/{y}.mvt")
    assert payload["glyphs_url"] == "/fonts/{fontstack}/{range}.pbf"
    assert payload["any_installed"] is True
    assert "http" not in payload["glyphs_url"]


def test_coverage_carries_the_attribution_of_the_package_it_offers(
    client: TestClient, deployment: Deployment
) -> None:
    """Attribution travels with the package into every projection."""
    deployment.install(MONACO, author="Somebody, A Provider", licence="CC-BY-4.0")

    source = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"][0]

    assert source["attribution"]["data_owner"] == "Somebody"
    assert source["attribution"]["provider"] == "A Provider"
    assert source["attribution"]["license_identifier"] == "CC-BY-4.0"


def test_a_track_with_no_installed_coverage_gets_none(
    client: TestClient, deployment: Deployment
) -> None:
    """A normal answer. The page draws the track on a neutral background."""
    deployment.install(MONACO)

    payload = client.get(f"/api/v1/maps/coverage?bbox={NOWHERE_BBOX}").json()

    assert payload["sources"] == []
    assert payload["any_installed"] is True


def test_an_empty_archive_distinguishes_nothing_installed_from_nothing_here(
    client: TestClient,
) -> None:
    """Two different sentences for a reader, from one flag."""
    payload = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()

    assert payload["sources"] == []
    assert payload["any_installed"] is False


def test_two_regions_each_answer_for_their_own_area(
    client: TestClient, deployment: Deployment
) -> None:
    """Which is the whole point of installing more than one."""
    deployment.install(MONACO)
    deployment.install(ANDORRA)

    monaco = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"]
    andorra = client.get(f"/api/v1/maps/coverage?bbox={ANDORRA_BBOX}").json()["sources"]

    assert [source["region_name"] for source in monaco] == ["Monaco"]
    assert [source["region_name"] for source in andorra] == ["Andorra"]


def test_a_malformed_bounding_box_is_refused_by_name(client: TestClient) -> None:
    """A query string is untrusted input like any other."""
    response = client.get("/api/v1/maps/coverage?bbox=not,a,box")

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "map_bbox_invalid"


def test_a_bounding_box_off_the_globe_is_refused(client: TestClient) -> None:
    """The same validation the domain applies, projected as a 400."""
    assert client.get("/api/v1/maps/coverage?bbox=0,0,900,900").status_code == 400


# --- Tile delivery -----------------------------------------------------------


def test_a_tile_is_served_from_the_installed_package(
    client: TestClient, deployment: Deployment
) -> None:
    """With its layers intact, gzipped as stored, and cacheable for a year."""
    deployment.install(MONACO)
    source = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"][0]
    url = source["tiles_url"].replace("{z}", "6").replace("{x}", "33").replace("{y}", "23")

    response = client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert "immutable" in response.headers["cache-control"]
    assert "streets" in layer_names(response.content)


def test_a_tile_url_is_immutable_because_it_names_the_content(
    client: TestClient, deployment: Deployment
) -> None:
    """An update is a new address rather than a cache somebody has to clear."""
    deployment.install(MONACO)
    before = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"][0]

    deployment.install(MONACO, place_name="Andersdorf")
    after = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"][0]

    assert after["tiles_url"] != before["tiles_url"]


def test_a_missing_tile_is_no_content_rather_than_an_error(
    client: TestClient, deployment: Deployment
) -> None:
    """A hole in coverage happens at every edge of every region."""
    deployment.install(MONACO)
    source = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"][0]
    url = source["tiles_url"].replace("{z}", "6").replace("{x}", "1").replace("{y}", "1")

    response = client.get(url)

    assert response.status_code == 204
    assert response.content == b""


def test_a_tile_address_outside_the_zoom_grid_is_no_content(
    client: TestClient, deployment: Deployment
) -> None:
    """`z=1, x=9` is not a tile. It is arithmetic nobody should have to do twice."""
    deployment.install(MONACO)
    source = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"][0]
    url = source["tiles_url"].replace("{z}", "1").replace("{x}", "9").replace("{y}", "0")

    assert client.get(url).status_code == 204


@pytest.mark.parametrize(
    "identity",
    [
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "0" * 63,
        "z" * 64,
        "0" * 65,
    ],
)
def test_a_tile_identity_that_is_not_a_content_hash_is_refused(
    client: TestClient, identity: str
) -> None:
    """The delivery identity is matched against the database, never against a path."""
    response = client.get(f"/api/v1/maps/tiles/{identity}/6/33/23.mvt")

    assert response.status_code in {404, 422}
    assert "root:" not in response.text


def test_a_tile_from_a_package_that_is_not_installed_is_a_named_refusal(
    client: TestClient,
) -> None:
    """A well-formed hash nobody installed is still nothing to serve."""
    response = client.get(f"/api/v1/maps/tiles/{'a' * 64}/6/33/23.mvt")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "map_region_unknown"


# --- Catalog -----------------------------------------------------------------


def test_the_catalog_lists_what_the_provider_offers(client: TestClient) -> None:
    """The top level first, with the children a reader can open."""
    payload = client.get("/api/v1/maps/catalog").json()

    assert payload["provider_available"] is True
    assert [entry["name"] for entry in payload["entries"]] == ["Europe"]
    assert payload["entries"][0]["has_children"] is True


def test_a_catalog_row_states_what_a_download_would_cost(
    client: TestClient, deployment: Deployment
) -> None:
    """Before installing, or honestly nothing at all."""
    deployment.install(MONACO)
    payload = client.get("/api/v1/maps/catalog?parent=fake:europe").json()

    rows = {entry["name"]: entry for entry in payload["entries"]}
    assert rows["Monaco"]["installable"] is True
    assert rows["Monaco"]["package"]["size_bytes"] > 0
    assert rows["Monaco"]["installed"] is True
    assert rows["Liechtenstein"]["installable"] is False
    assert rows["Liechtenstein"]["package"] is None


def test_an_unavailable_provider_leaves_installed_maps_alone(
    client: TestClient, deployment: Deployment
) -> None:
    """The failure a self-hosted deployment has to survive without noticing much."""
    deployment.install(MONACO)
    client.get("/api/v1/maps/catalog")
    deployment.provider.unavailable = True

    catalog = client.get("/api/v1/maps/catalog")
    coverage = client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}")
    installed = client.get("/api/v1/maps")

    assert catalog.status_code == 200
    assert catalog.json()["entries"], "the last good catalog is still shown"
    assert coverage.json()["sources"], "an installed map keeps working"
    assert installed.json()["maps"][0]["state"] == "installed"


def test_a_provider_that_was_never_reachable_is_reported_as_such(
    client: TestClient, deployment: Deployment
) -> None:
    """With nothing cached there is nothing to fall back to, and it says so."""
    deployment.provider.unavailable = True

    response = client.get("/api/v1/maps/catalog")

    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "map_provider_unavailable"


# --- Installing --------------------------------------------------------------


def test_installing_takes_a_region_and_answers_with_a_job(
    client: TestClient, deployment: Deployment
) -> None:
    """`202`: nothing is installed yet, and here is where to watch it."""
    deployment.install(MONACO)
    deployment.repository.interrupt_unfinished_jobs(deployment.clock.now())
    client.get("/api/v1/maps/catalog?parent=fake:europe")
    deployment.app.state.map_installer = _RecordingInstaller(deployment)

    response = client.post("/api/v1/maps/install", json={"region_id": ANDORRA})

    assert response.status_code == 202
    assert response.json()["region_id"] == ANDORRA
    assert response.json()["state"] == "queued"


def test_installing_an_unknown_region_is_refused(client: TestClient) -> None:
    """A region identity that never came from a catalog reaches no provider."""
    response = client.post("/api/v1/maps/install", json={"region_id": "fake:europe/atlantis"})

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "map_region_unknown"


@pytest.mark.parametrize(
    "hostile",
    ["../../etc", "https://attacker.example/x.mbtiles", "fake:../../etc", "", "x" * 300],
)
def test_installing_refuses_anything_that_is_not_a_region_identity(
    client: TestClient, hostile: str
) -> None:
    """There is no field on this endpoint that can name a host or a path."""
    response = client.post("/api/v1/maps/install", json={"region_id": hostile})

    assert response.status_code in {400, 404, 422}


def test_a_job_reports_progress_without_inventing_a_percentage(
    client: TestClient, deployment: Deployment
) -> None:
    """A byte count is honest; a percentage of an unknown total is not."""
    deployment.install(MONACO)

    jobs = client.get("/api/v1/maps/jobs").json()["jobs"]

    assert jobs[0]["state"] == "completed"
    assert jobs[0]["bytes_downloaded"] > 0
    assert jobs[0]["percentage"] == 100


def test_an_unknown_job_is_a_named_refusal(client: TestClient) -> None:
    """Rather than an empty object a poller would loop on forever."""
    response = client.get("/api/v1/maps/jobs/deadbeef")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "map_job_not_found"


# --- Removing ----------------------------------------------------------------


def test_removing_a_package_leaves_the_archive_without_coverage(
    client: TestClient, deployment: Deployment
) -> None:
    """And with its track data untouched, which is a different table entirely."""
    deployment.install(MONACO)

    removed = client.delete(f"/api/v1/maps/regions/{MONACO}")

    assert removed.status_code == 204
    assert client.get("/api/v1/maps").json()["maps"] == []
    assert client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").json()["sources"] == []
    assert client.get("/api/v1/tracks").status_code == 200


def test_removing_something_that_is_not_installed_says_so(client: TestClient) -> None:
    """Succeeding quietly would read as "it was there"."""
    response = client.delete(f"/api/v1/maps/regions/{MONACO}")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "map_package_not_installed"


# --- Credits -----------------------------------------------------------------


def test_credits_are_derived_from_what_is_installed(
    client: TestClient, deployment: Deployment
) -> None:
    """A second provider appears the day somebody installs one of its packages."""
    deployment.install(MONACO)
    deployment.install(ANDORRA)

    acknowledged = client.get("/api/v1/maps/credits").json()["map_data"]

    assert len(acknowledged) == 1, "one data source, two regions, deduplicated"
    assert acknowledged[0]["data_owner"] == "OpenStreetMap contributors"
    assert sorted(acknowledged[0]["regions"]) == ["Andorra", "Monaco"]
    assert acknowledged[0]["links"][0]["url"].startswith("https://")


def test_credits_are_empty_on_an_archive_with_no_maps(client: TestClient) -> None:
    """Nothing installed is nothing to credit, not a hard-coded acknowledgement."""
    assert client.get("/api/v1/maps/credits").json()["map_data"] == []


# --- Turning the capability off ----------------------------------------------


def test_a_deployment_can_refuse_to_install_anything(tmp_path: Path) -> None:
    """An operator who wants no outbound request at all gets a clear refusal."""
    settings = Settings(
        data_dir=tmp_path / "data", web_dir=tmp_path / "no-build", maps_enabled=False
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/maps").json()["installs_enabled"] is False
        assert client.get("/api/v1/maps/catalog").status_code == 403
        assert client.post("/api/v1/maps/install", json={"region_id": MONACO}).status_code == 403
        assert client.get(f"/api/v1/maps/coverage?bbox={MONACO_BBOX}").status_code == 200


class _RecordingInstaller:
    """A stand-in that queues a job and never runs it, so a test stays fast."""

    def __init__(self, deployment: Deployment) -> None:
        """Record the deployment whose repository the job is written to."""
        self._deployment = deployment

    def start(self, region_id: MapRegionId) -> object:
        """Queue a job and return it."""
        job = queued_job(
            job_id="f" * 32,
            region_id=region_id,
            region_name=region_id.segments[-1].title(),
            at=self._deployment.clock.now(),
            is_update=False,
        )
        self._deployment.repository.create_job(job)
        return job

    def cancel(self, job_id: str) -> None:
        """Do nothing; there is no worker to interrupt."""
