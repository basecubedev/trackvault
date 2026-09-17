"""Build the archive the documentation screenshots are taken of.

Everything this writes is invented. The journeys come from `demo_region`, the
clock comes from a schedule in this file, and the receiver noise comes from a
seeded generator -- so the archive is the same one every time, and it is nobody's.

The set is chosen to put every state the interface can show into a picture:
several years and every month of the newest one, four activities, recordings
with sensors and without, a planned route with a synthetic clock, a route with
no clock at all, a track the evidence does not decide, and one afternoon that
arrived twice in two different files.

Run it with `TRACKVAULT_DATA_DIR` pointing at a throwaway directory:

    TRACKVAULT_DATA_DIR=/tmp/demo uv run --project .. python scripts/demo_archive.py
"""

from __future__ import annotations

import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_map  # noqa: E402
import demo_region  # noqa: E402

from support.fake_provider import FakeMapProvider  # noqa: E402
from support.fake_provider import region as catalog_region  # noqa: E402
from support.map_packages import build_package  # noqa: E402
from trackvault.application.maps import (  # noqa: E402
    CatalogRegion,
    GetMapCatalog,
    InstallMapPackage,
    queued_job,
)
from trackvault.cli import main as run_cli  # noqa: E402
from trackvault.config import Settings  # noqa: E402
from trackvault.domain.maps import MapRegionId  # noqa: E402
from trackvault.infrastructure.database import SqliteTrackStore  # noqa: E402
from trackvault.infrastructure.database.map_store import SqliteMapPackageStore  # noqa: E402
from trackvault.infrastructure.maps import (  # noqa: E402
    FilesystemMapCatalogCache,
    FilesystemMapPackageStorage,
    MbtilesPackageInspector,
)

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
GARMIN_NAMESPACE = "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"
LOCUS_NAMESPACE = "https://www.locusmap.app"

SAMPLE_SECONDS = 5
"""How often the imaginary receiver writes a position."""


@dataclass(frozen=True, slots=True)
class Outing:
    """One entry in the demo archive's calendar.

    Attributes:
        journey: Which route through the region was travelled, by name.
        start: When it started, in the archive's own timezone.
        title: What the file calls it.
        kind: `recorded`, `planned` or `undecided` -- which evidence the
            document carries, not a field anybody may set. A recording carries
            receiver quality, a planned route carries turn instructions, and an
            undecided one carries neither, which is exactly why the classifier
            answers `unknown` for it.
        sensors: Whether a heart rate strap and a cadence sensor were worn.
        clock: Whether the document carries timestamps at all.
        reverse: Walk the journey the other way round.
        pace: Multiplies the pace, so the same route is not always the same time.
    """

    journey: str
    start: datetime
    title: str
    kind: str = "recorded"
    sensors: bool = False
    clock: bool = True
    reverse: bool = False
    pace: float = 1.0


def _at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return one instant in UTC, which is the timezone the demo aggregates in."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


CALENDAR = (
    # 2024: enough for the dashboard's "All years" view to have a second bar.
    Outing("Harbour loop", _at(2024, 4, 13, 9, 30), "Harbour loop in the rain"),
    Outing("Coast road to Storvika", _at(2024, 5, 18, 8, 0), "First ride of the year"),
    Outing("Raudfjell from Nordvik", _at(2024, 6, 22, 7, 15), "Raudfjell, long way up"),
    Outing("Around the island", _at(2024, 7, 6, 6, 45), "All the way round", sensors=True),
    Outing("South shore track", _at(2024, 9, 14, 15, 0), "Late afternoon at the shore"),
    Outing("Storknuten circuit", _at(2024, 10, 5, 10, 0), "Storknuten before the weather"),
    # 2025: the year the screenshots are of, with something in most months.
    Outing("Harbour loop", _at(2025, 1, 11, 10, 30), "Cold harbour loop"),
    Outing("South shore track", _at(2025, 1, 26, 14, 0), "Shore walk, low sun"),
    Outing("Blavatnet and back", _at(2025, 2, 15, 11, 0), "Out to Blavatnet"),
    Outing("Sanddal river run", _at(2025, 2, 23, 8, 30), "River loop", sensors=True, pace=1.1),
    Outing("Storknuten circuit", _at(2025, 3, 9, 9, 0), "Storknuten in the wind"),
    Outing("Coast road to Storvika", _at(2025, 3, 22, 9, 45), "Coast road, easy pace"),
    Outing("Lyngfjell out and back", _at(2025, 4, 6, 7, 30), "Lyngfjell repeats", sensors=True),
    Outing("Raudfjell from Nordvik", _at(2025, 4, 19, 8, 0), "Raudfjell with the family"),
    Outing("Around the island", _at(2025, 5, 3, 6, 30), "Round the island", sensors=True),
    Outing("Harbour loop", _at(2025, 5, 17, 18, 15), "Evening harbour loop"),
    Outing("Storvika to Lyngholm", _at(2025, 5, 31, 9, 0), "Over to Lyngholm", sensors=True),
    Outing("Raudfjell and the ridge", _at(2025, 6, 14, 6, 0), "The whole ridge"),
    Outing("Sanddal river run", _at(2025, 6, 28, 7, 0), "River run", sensors=True, pace=0.95),
    Outing("Coast road to Storvika", _at(2025, 7, 5, 7, 30), "Storvika and back"),
    Outing("South shore track", _at(2025, 7, 19, 17, 0), "Shore in the evening"),
    Outing(
        "Storvika to Lyngholm",
        _at(2025, 7, 27, 6, 15),
        "Lyngholm the long way",
        sensors=True,
        pace=0.9,
    ),
    Outing("Lyngfjell out and back", _at(2025, 8, 9, 7, 45), "Lyngfjell, hot"),
    Outing("Blavatnet and back", _at(2025, 8, 24, 10, 30), "Swim at Blavatnet"),
    Outing("Raudfjell from Nordvik", _at(2025, 9, 7, 8, 15), "Raudfjell, clear day", sensors=True),
    Outing("Storvika to Lyngholm", _at(2025, 9, 21, 9, 30), "Lyngholm loop"),
    Outing("Raudfjell and the ridge", _at(2025, 10, 4, 7, 0), "Ridge traverse", sensors=True),
    Outing("Harbour loop", _at(2025, 10, 18, 11, 0), "Harbour loop with the dog", reverse=True),
    Outing("Storknuten circuit", _at(2025, 11, 8, 9, 15), "Storknuten, first frost"),
    Outing("South shore track", _at(2025, 11, 22, 13, 30), "Shore, grey"),
    Outing("Harbour loop", _at(2025, 12, 13, 10, 0), "Last walk of the year"),
    # What the archive is careful about, one document each.
    Outing(
        "Raudfjell and the ridge",
        _at(2026, 4, 11, 8, 0),
        "Ridge traverse (planned)",
        kind="planned",
    ),
    Outing(
        "Storvika to Lyngholm",
        _at(2026, 5, 2, 9, 0),
        "Lyngholm route (planned, no clock)",
        kind="planned",
        clock=False,
    ),
    Outing(
        "Blavatnet and back",
        _at(2025, 3, 30, 11, 0),
        "Blavatnet, exported without the receiver log",
        kind="undecided",
    ),
)

DUPLICATED = "Ridge traverse"
"""The outing that also arrives as a second file, to show the `2 imports` badge.

The same positions at the same instants, written as GPX 1.0 instead of 1.1: a
different document carrying the same afternoon, which is the case the archive
recognises and the list collapses into one row.
"""

BASE_SPEED = {
    "walking": 1.35,
    "hiking": 1.10,
    "running": 2.95,
    "cycling": 5.60,
}
"""Metres per second on the flat, before the ground has its say."""


@dataclass(slots=True)
class Sample:
    """One position the imaginary receiver wrote."""

    longitude: float
    latitude: float
    elevation: float
    second: int
    heart_rate: int | None
    cadence: int | None
    course: float


def _resample(waypoints: list[demo_region.Metres], spacing: float) -> list[demo_region.Metres]:
    """Return the route as points an even distance apart along it."""
    walked: list[demo_region.Metres] = [waypoints[0]]
    carried = 0.0
    for start, end in pairwise(waypoints):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length == 0.0:
            continue
        position = carried
        while position + spacing <= length:
            position += spacing
            fraction = position / length
            walked.append(
                (
                    start[0] + (end[0] - start[0]) * fraction,
                    start[1] + (end[1] - start[1]) * fraction,
                )
            )
        carried = position - length
    return walked


def _travel(outing: Outing, journey: demo_region.Journey) -> list[Sample]:
    """Return the positions one outing produced.

    The traveller moves at a pace the gradient argues with, stops twice, and is
    followed by a receiver that is a metre or two out and cannot quite agree
    with itself about altitude. All of it is seeded on the outing's own title,
    so the same outing produces the same track every time this runs.
    """
    noise = random.Random(outing.title)  # noqa: S311 - scenery, not security
    waypoints = list(reversed(journey.waypoints)) if outing.reverse else journey.waypoints
    speed = BASE_SPEED[journey.activity] / outing.pace
    line = _resample(waypoints, 4.0)

    # Where the traveller stands still for a while. Positions keep arriving, so
    # the archive can tell a rest from a receiver that stopped writing.
    rests = sorted(noise.sample(range(len(line) // 6, len(line) * 5 // 6), 2))
    rest_seconds = {index: noise.randint(150, 420) for index in rests}

    samples: list[Sample] = []
    second = 0
    travelled = 0.0
    index = 0
    drift = 0.0
    while index < len(line) - 1:
        here = line[index]
        ground = demo_region.elevation(here)
        ahead = line[min(index + 12, len(line) - 1)]
        rise = demo_region.elevation(ahead) - ground
        run = max(1.0, math.hypot(ahead[0] - here[0], ahead[1] - here[1]))
        gradient = max(-0.25, min(0.25, rise / run))
        # Uphill costs more than downhill returns, which is why an out-and-back
        # is slower than the same distance on the flat.
        pace = speed * math.exp(-4.2 * gradient if gradient > 0 else -1.1 * gradient)
        pace *= 0.96 + 0.08 * noise.random()

        drift = drift * 0.88 + noise.gauss(0.0, 0.9)
        heading = math.degrees(math.atan2(ahead[0] - here[0], ahead[1] - here[1])) % 360.0
        samples.append(
            Sample(
                *demo_region.to_degrees(
                    (here[0] + noise.gauss(0.0, 0.5), here[1] + noise.gauss(0.0, 0.5))
                ),
                elevation=ground + drift,
                second=second,
                heart_rate=_heart_rate(journey.activity, gradient, noise)
                if outing.sensors
                else None,
                cadence=_cadence(journey.activity, pace, noise) if outing.sensors else None,
                course=heading,
            )
        )

        if index in rest_seconds:
            for _ in range(rest_seconds.pop(index) // SAMPLE_SECONDS):
                second += SAMPLE_SECONDS
                samples.append(
                    Sample(
                        *demo_region.to_degrees(
                            (here[0] + noise.gauss(0.0, 0.7), here[1] + noise.gauss(0.0, 0.7))
                        ),
                        elevation=ground + drift,
                        second=second,
                        heart_rate=_heart_rate(journey.activity, -0.2, noise)
                        if outing.sensors
                        else None,
                        cadence=0 if outing.sensors and journey.activity == "cycling" else None,
                        course=heading,
                    )
                )

        second += SAMPLE_SECONDS
        travelled += pace * SAMPLE_SECONDS
        index = int(travelled / 4.0)
    return samples


def _heart_rate(activity: str, gradient: float, noise: random.Random) -> int:
    """Return a plausible heart rate for how hard this moment is."""
    base = {"walking": 104, "hiking": 118, "running": 158, "cycling": 134}[activity]
    return max(72, min(192, round(base + 190.0 * gradient + noise.gauss(0.0, 4.0))))


STRIDE = {"walking": 0.76, "hiking": 0.70, "running": 1.12}
"""Metres per step, which is what turns a pace into steps per minute."""

DEVELOPMENT = 4.6
"""Metres a bicycle travels per pedal revolution, in the gear these rides use."""


def _cadence(activity: str, pace: float, noise: random.Random) -> int:
    """Return a plausible cadence, in the unit the activity is counted in.

    Revolutions per minute on a bicycle and steps per minute on foot. The two
    are different quantities in one field, which is what the exchange format
    offers and what the archive passes through unchanged.
    """
    metres_per_cycle = DEVELOPMENT if activity == "cycling" else STRIDE[activity]
    return max(0, round(60.0 * pace / metres_per_cycle + noise.gauss(0.0, 2.5)))


def _document(outing: Outing, journey: demo_region.Journey, samples: list[Sample]) -> str:
    """Return the GPX document one outing is delivered as."""
    recorded = outing.kind == "recorded"
    planned = outing.kind == "planned"
    namespaces = [f'xmlns="{GPX_NAMESPACE}"']
    if recorded:
        namespaces.append(f'xmlns:gpxtpx="{GARMIN_NAMESPACE}"')
    if planned:
        namespaces.append(f'xmlns:locus="{LOCUS_NAMESPACE}"')

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<gpx version="1.1" creator="{_creator(outing)}" {" ".join(namespaces)}>',
        "  <metadata>",
        f"    <name>{outing.title}</name>",
    ]
    if outing.clock:
        exported = outing.start + timedelta(seconds=samples[-1].second + 3_600)
        lines.append(f"    <time>{_instant(exported)}</time>")
    lines.append("  </metadata>")
    if planned:
        lines.extend(_instructions(samples))
    lines.extend(
        [
            "  <trk>",
            f"    <name>{outing.title}</name>",
            f"    <type>{journey.activity}</type>",
            "    <trkseg>",
        ]
    )
    for sample in samples:
        lines.append(_position(sample, outing, recorded))
    lines.extend(["    </trkseg>", "  </trk>", "</gpx>", ""])
    return "\n".join(lines)


def _creator(outing: Outing) -> str:
    """Return what the document says wrote it.

    Deliberately a name no classifier may act on: the exporting application is
    provenance, and a track's kind is decided from the evidence in the document.
    """
    return "DemoRoutePlanner 2.0" if outing.kind == "planned" else "DemoRecorder 3.1"


TURNS = ((0.25, "Turn left", 3), (0.75, "Turn right", 4))
"""Where the route turns, what it is called, and the action code Locus writes.

The code is the evidence. A waypoint with a name is a marker somebody dropped;
a waypoint carrying a navigation action is an instruction, which is what makes a
document a *planned* route rather than one that merely has no receiver log.
"""


def _instructions(samples: list[Sample]) -> list[str]:
    """Return the turn-by-turn waypoints that make a document a planned route."""
    lines = []
    for fraction, name, action in TURNS:
        sample = samples[int(len(samples) * fraction)]
        lines.extend(
            [
                f'  <wpt lat="{sample.latitude:.7f}" lon="{sample.longitude:.7f}">',
                f"    <name>{name}</name>",
                "    <extensions>",
                f"      <locus:rtePointAction>{action}</locus:rtePointAction>",
                "    </extensions>",
                "  </wpt>",
            ]
        )
    return lines


def _position(sample: Sample, outing: Outing, recorded: bool) -> str:
    """Return one `<trkpt>`, with exactly the evidence its document has."""
    parts = [
        f'      <trkpt lat="{sample.latitude:.7f}" lon="{sample.longitude:.7f}">',
        f"<ele>{sample.elevation:.1f}</ele>",
    ]
    if outing.clock:
        parts.append(f"<time>{_instant(outing.start + timedelta(seconds=sample.second))}</time>")
    if recorded:
        parts.append("<hdop>1.2</hdop>")
        readings = [f"<gpxtpx:course>{sample.course:.1f}</gpxtpx:course>"]
        if sample.heart_rate is not None:
            readings.insert(0, f"<gpxtpx:hr>{sample.heart_rate}</gpxtpx:hr>")
        if sample.cadence is not None:
            readings.insert(-1, f"<gpxtpx:cad>{sample.cadence}</gpxtpx:cad>")
        parts.append(
            "<extensions><gpxtpx:TrackPointExtension>"
            + "".join(readings)
            + "</gpxtpx:TrackPointExtension></extensions>"
        )
    parts.append("</trkpt>")
    return "".join(parts)


def _as_gpx_10(document: str) -> str:
    """Return the same recording as a GPX 1.0 document.

    Same positions, same instants, different bytes -- which is what makes it a
    second *import* of one afternoon rather than a second track.
    """
    body = document.split("\n")
    rewritten = []
    for line in body:
        if line.startswith("<gpx "):
            rewritten.append(
                '<gpx version="1.0" creator="DemoRecorder 3.1" '
                'xmlns="http://www.topografix.com/GPX/1/0">'
            )
            continue
        if "<extensions>" in line:
            line = line[: line.index("<extensions>")] + "</trkpt>"
        rewritten.append(line)
    return "\n".join(rewritten)


def _instant(moment: datetime) -> str:
    """Return one instant in the shape a GPX document writes it."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_documents(directory: Path) -> list[Path]:
    """Write every demo document and return the paths, in import order."""
    directory.mkdir(parents=True, exist_ok=True)
    journeys = {journey.name: journey for journey in demo_region.JOURNEYS}
    written = []
    for outing in CALENDAR:
        journey = journeys[outing.journey]
        document = _document(outing, journey, _travel(outing, journey))
        path = directory / f"{outing.start:%Y-%m-%d}-{_slug(outing.title)}.gpx"
        path.write_text(document, encoding="utf-8")
        written.append(path)
        if outing.title == DUPLICATED:
            older = directory / f"{outing.start:%Y-%m-%d}-{_slug(outing.title)}-gpx10.gpx"
            older.write_text(_as_gpx_10(document), encoding="utf-8")
            written.append(older)
    return written


def _slug(title: str) -> str:
    """Return a title as a filename, the way a sync client would."""
    return "".join(letter if letter.isalnum() else "-" for letter in title.lower()).strip("-")


def install_map(settings: Settings) -> None:
    """Build the demo island as a map package and install it, offline."""
    storage = FilesystemMapPackageStorage(settings.map_storage_dir)
    storage.prepare()
    store = SqliteTrackStore(settings.database_path)
    store.migrate()
    clock = _FixedClock()

    package = settings.data_dir / "demo-map.mbtiles"
    build_package(
        package,
        bounds=demo_map.bounds(),
        min_zoom=demo_map.MIN_ZOOM,
        max_zoom=demo_map.MAX_ZOOM,
        author=demo_map.AUTHOR,
        licence=demo_map.LICENCE,
        layers=(*demo_map_layers(),),
        features=demo_map.features,
        description="Synthetic basemap for the TrackVault documentation",
    )
    provider = FakeMapProvider(
        regions=_catalog(),
        packages={demo_map.REGION_ID: package},
        slug=demo_map.PROVIDER_SLUG,
        display_name=demo_map.PROVIDER_NAME,
    )
    catalog = GetMapCatalog(
        provider=provider,
        cache=FilesystemMapCatalogCache(storage.catalog_directory()),
        clock=clock,
    )
    repository = SqliteMapPackageStore(store)
    installer = InstallMapPackage(
        provider=provider,
        catalog=catalog,
        storage=storage,
        inspector=MbtilesPackageInspector(storage),
        repository=repository,
        clock=clock,
    )
    job = queued_job(
        job_id="d" * 32,
        region_id=MapRegionId.parse(demo_map.REGION_ID),
        region_name=demo_map.REGION_NAME,
        at=clock.now(),
        is_update=False,
    )
    repository.create_job(job)
    installer.run(job, is_cancelled=lambda: False)
    package.unlink(missing_ok=True)


def demo_map_layers() -> tuple[str, ...]:
    """Return the vector layers the demo package carries."""
    return (
        "boundaries",
        "land",
        "ocean",
        "place_labels",
        "street_labels",
        "streets",
        "water_lines",
        "water_polygons",
    )


def _catalog() -> tuple[CatalogRegion, ...]:
    """Return the one-region catalog the demo provider offers.

    Deliberately only what the installer has to resolve. The catalog a reader
    browses on the offline maps page comes from the deployment's own provider,
    which is Geofabrik and is not this -- and inventing regions under somebody
    else's name is the kind of claim a screenshot must not make.
    """
    west, south, east, north = demo_map.bounds()
    return (
        catalog_region(demo_map.REGION_ID, demo_map.REGION_NAME, bounds=(west, south, east, north)),
    )


class _FixedClock:
    """A clock that does not move, so the demo archive is reproducible."""

    def now(self) -> datetime:
        """Return the instant the demo was built at."""
        return datetime(2026, 1, 12, 9, 0, tzinfo=UTC)


def main() -> int:
    """Write the demo documents, import them, and install the demo basemap."""
    if not os.environ.get("TRACKVAULT_DATA_DIR"):
        sys.stderr.write("TRACKVAULT_DATA_DIR must point at a throwaway directory\n")
        return 2
    settings = Settings()
    documents = write_documents(settings.data_dir / "demo-files")
    imported = run_cli(["import", *(str(path) for path in documents)], settings=settings)
    if imported != 0:
        return imported
    install_map(settings)
    sys.stdout.write(f"demo archive ready: {len(documents)} documents\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
