"""The map vocabulary: identity, extent, attribution and which map to draw.

Everything here is pure. No file system, no network, no database -- which is the
point of putting these rules in the domain, and the reason a coverage decision
can be reasoned about without installing anything.
"""

from datetime import UTC, datetime

import pytest

from gpx_view.domain.maps import (
    AttributionLink,
    MapAttribution,
    MapBounds,
    MapPackage,
    MapPackageFormat,
    MapRegionId,
    MapTileSchema,
    select_coverage,
)

pytestmark = [pytest.mark.unit, pytest.mark.maps]


# --- Region identity ---------------------------------------------------------


def test_a_region_is_named_by_its_provider_and_its_path() -> None:
    """“Limburg" is a Dutch province and a Belgian one; the scope decides."""
    region = MapRegionId.parse("geofabrik:europe/germany/bayern")

    assert region.provider == "geofabrik"
    assert region.segments == ("europe", "germany", "bayern")
    assert str(region) == "geofabrik:europe/germany/bayern"


def test_a_region_knows_the_one_above_it() -> None:
    """Which is how a catalog is browsed and how a parent fallback is found."""
    region = MapRegionId.parse("geofabrik:europe/germany/bayern")

    assert str(region.parent) == "geofabrik:europe/germany"
    assert str(region.parent.parent) == "geofabrik:europe"  # type: ignore[union-attr]
    assert region.parent.parent.parent is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "hostile",
    [
        "geofabrik:../../etc/passwd",
        "geofabrik:europe/../../../etc",
        "geofabrik:europe/./germany",
        "geofabrik:/europe/germany",
        "geofabrik:europe//germany",
        "geofabrik:europe/germany/",
        "geofabrik:europe/Germany",
        "geofabrik:europe/germany\\bayern",
        "geofabrik:",
        "europe/germany",
        "geofabrik:a/b/c/d/e/f/g",
    ],
)
def test_a_region_identity_cannot_express_a_path(hostile: str) -> None:
    """The character class has no `.` in it, so `..` is unwriteable, not merely refused."""
    with pytest.raises(ValueError, match=r"map region|region identity"):
        MapRegionId.parse(hostile)


def test_the_storage_key_is_a_digest_rather_than_the_identity() -> None:
    """The second of two independent reasons nothing escapes the managed root.

    The identity is already validated to characters that cannot form a path.
    This is what still holds if that validation is ever wrong.
    """
    key = MapRegionId.parse("geofabrik:europe/germany/bayern").storage_key

    assert len(key) == 64
    assert set(key) <= set("0123456789abcdef")


def test_the_storage_key_is_stable_across_runs() -> None:
    """An update has to find the directory the install wrote."""
    first = MapRegionId.parse("geofabrik:europe/monaco").storage_key
    second = MapRegionId.parse("geofabrik:europe/monaco").storage_key

    assert first == second


def test_two_providers_offering_the_same_path_are_two_regions() -> None:
    """Provider scoping is identity, not decoration."""
    assert MapRegionId.parse("geofabrik:europe/monaco") != MapRegionId.parse("other:europe/monaco")


# --- Bounds ------------------------------------------------------------------


def test_bounds_refuse_a_rectangle_that_is_off_the_globe() -> None:
    """Remote metadata says what it likes; a rectangle is checked."""
    with pytest.raises(ValueError, match=r"off the globe|within"):
        MapBounds(min_longitude=0.0, min_latitude=-91.0, max_longitude=1.0, max_latitude=1.0)


def test_bounds_refuse_an_inside_out_rectangle() -> None:
    """`min > max` is not a small rectangle, it is a broken one."""
    with pytest.raises(ValueError, match="ordered"):
        MapBounds(min_longitude=10.0, min_latitude=0.0, max_longitude=5.0, max_latitude=1.0)


def test_bounds_refuse_a_value_that_is_not_a_number() -> None:
    """A NaN comparison is false in both directions, which would pass every check."""
    with pytest.raises(ValueError, match="finite"):
        MapBounds(min_longitude=float("nan"), min_latitude=0.0, max_longitude=1.0, max_latitude=1.0)


def test_a_rectangle_contains_one_that_touches_its_edge() -> None:
    """A track along a region's border is inside the region."""
    region = MapBounds(min_longitude=0.0, min_latitude=0.0, max_longitude=10.0, max_latitude=10.0)
    track = MapBounds(min_longitude=0.0, min_latitude=5.0, max_longitude=3.0, max_latitude=6.0)

    assert region.covers(track)
    assert region.intersects(track)


def test_padding_a_rectangle_stops_at_the_poles() -> None:
    """A track near the Arctic must not produce a rectangle off the globe."""
    padded = MapBounds(
        min_longitude=-179.99, min_latitude=89.99, max_longitude=-179.9, max_latitude=89.999
    ).padded(1.0)

    assert padded.min_longitude == -180.0
    assert padded.max_latitude == 90.0


def test_bounds_around_no_positions_is_nowhere_rather_than_the_origin() -> None:
    """A track with no positions occupies nowhere. `(0, 0)` is the Gulf of Guinea."""
    assert MapBounds.around(()) is None


# --- Attribution -------------------------------------------------------------


def test_an_attribution_link_must_be_plain_https() -> None:
    """A credit link is a link, not a script."""
    for hostile in ("javascript:alert(1)", "http://example.test/", "data:text/html,x", "https://"):
        with pytest.raises(ValueError, match="https"):
            AttributionLink(label="x", url=hostile)


def test_attribution_refuses_a_control_character() -> None:
    """Provider metadata that carries a newline is metadata this build cannot render."""
    with pytest.raises(ValueError, match="control characters"):
        AttributionLink(label="two\nlines", url="https://example.test/")


def test_attribution_refuses_a_blank_required_text() -> None:
    """An attribution that renders as nothing is not an attribution."""
    with pytest.raises(ValueError, match="must not be blank"):
        _attribution(required_text="   ")


# --- Package -----------------------------------------------------------------


def test_a_package_is_identified_for_delivery_by_its_content_hash() -> None:
    """Which is what makes a tile URL immutable and cacheable for a year."""
    package = _package("geofabrik:europe/monaco", (7.4, 43.4, 7.6, 43.8))

    assert package.delivery_id == package.content_sha256


def test_a_package_refuses_a_hash_that_could_not_have_been_computed() -> None:
    """The hash reaches a file name. It is checked where it is created."""
    with pytest.raises(ValueError, match="hexadecimal"):
        _package("geofabrik:europe/monaco", (7.4, 43.4, 7.6, 43.8), digest="../../etc/passwd")


def test_a_package_refuses_a_provider_that_disagrees_with_its_region() -> None:
    """Two statements of where a package came from is one too many."""
    with pytest.raises(ValueError, match="provider"):
        _package("geofabrik:europe/monaco", (7.4, 43.4, 7.6, 43.8), provider="somebody-else")


def test_a_package_refuses_a_download_time_without_a_zone() -> None:
    """“When did I get this?" has to mean the same thing to two readers."""
    with pytest.raises(ValueError, match="unambiguous instant"):
        _package(
            "geofabrik:europe/monaco",
            (7.4, 43.4, 7.6, 43.8),
            downloaded_at=datetime(2026, 8, 9, 12, 0),
        )


# --- Coverage selection ------------------------------------------------------

BAVARIA = (8.9, 47.2, 13.9, 50.6)
GERMANY = (5.8, 47.2, 15.1, 55.1)
NETHERLANDS = (3.3, 50.7, 7.3, 53.6)
MUNICH = MapBounds(min_longitude=11.4, min_latitude=48.0, max_longitude=11.7, max_latitude=48.2)


def test_no_installed_map_is_a_normal_answer() -> None:
    """A track in a country nobody downloaded still draws, on a neutral background."""
    assert select_coverage(MUNICH, ()) == ()


def test_the_smallest_package_that_covers_a_track_wins() -> None:
    """Germany and Bavaria both contain Munich. Bavaria holds it in fewer bytes."""
    germany = _package("geofabrik:europe/germany", GERMANY)
    bavaria = _package("geofabrik:europe/germany/bayern", BAVARIA)

    chosen = select_coverage(MUNICH, (germany, bavaria))

    assert [package.region_name for package in chosen] == ["europe/germany/bayern"]


def test_a_parent_is_used_when_the_child_is_not_installed() -> None:
    """Which is the whole reason a parent fallback exists."""
    germany = _package("geofabrik:europe/germany", GERMANY)

    assert select_coverage(MUNICH, (germany,)) == (germany,)


def test_parent_and_child_never_draw_the_same_ground_twice() -> None:
    """Two sources over one area collide labels and darken every fill.

    It looks like a rendering fault rather than like a selection mistake, which
    is exactly why it has to be impossible rather than unlikely.
    """
    germany = _package("geofabrik:europe/germany", GERMANY)
    bavaria = _package("geofabrik:europe/germany/bayern", BAVARIA)

    assert len(select_coverage(MUNICH, (germany, bavaria))) == 1


def test_a_track_crossing_a_border_keeps_a_basemap_on_both_sides() -> None:
    """No single package covers it, so both intersecting ones are offered."""
    germany = _package("geofabrik:europe/germany", GERMANY)
    netherlands = _package("geofabrik:europe/netherlands", NETHERLANDS)
    crossing = MapBounds(min_longitude=6.0, min_latitude=51.0, max_longitude=7.0, max_latitude=51.5)

    chosen = select_coverage(crossing, (germany, netherlands))

    assert {package.region_name for package in chosen} == {
        "europe/germany",
        "europe/netherlands",
    }


def test_a_neighbour_is_kept_even_though_the_rectangles_overlap() -> None:
    """Germany's bounding box reaches into the Netherlands, and geometry cannot tell.

    Both countries "cover" a rectangle near Aachen, and only one of them holds
    data on each side of the border. Deciding by rectangle would blank half the
    map; deciding by the provider's own region tree keeps both, because neither
    is inside the other.
    """
    germany = _package("geofabrik:europe/germany", GERMANY)
    netherlands = _package("geofabrik:europe/netherlands", NETHERLANDS)
    aachen = MapBounds(
        min_longitude=5.95, min_latitude=50.75, max_longitude=6.15, max_latitude=50.85
    )

    assert germany.bounds.covers(aachen) and netherlands.bounds.covers(aachen)
    assert len(select_coverage(aachen, (germany, netherlands))) == 2


def test_a_parent_wins_when_the_child_alone_would_leave_a_gap() -> None:
    """A track from Bavaria into Hessen is not a Bavarian track.

    Bavaria does not cover it, Germany does, and drawing both would render
    Bavaria twice for no gain. The specific package is dropped here, which is
    the opposite of the Munich case and the reason the rule is about covering
    rather than about being small.
    """
    germany = _package("geofabrik:europe/germany", GERMANY)
    bavaria = _package("geofabrik:europe/germany/bayern", BAVARIA)
    across = MapBounds(min_longitude=9.0, min_latitude=49.5, max_longitude=9.5, max_latitude=51.0)

    chosen = select_coverage(across, (germany, bavaria))

    assert [package.region_name for package in chosen] == ["europe/germany"]


def test_a_track_outside_every_installed_region_gets_nothing() -> None:
    """Rather than the nearest map, which would be a map of somewhere else."""
    germany = _package("geofabrik:europe/germany", GERMANY)
    patagonia = MapBounds(
        min_longitude=-72.0, min_latitude=-51.0, max_longitude=-71.0, max_latitude=-50.0
    )

    assert select_coverage(patagonia, (germany,)) == ()


def test_the_selection_does_not_depend_on_the_order_it_was_given() -> None:
    """A basemap that changes between two page loads is unreproducible by design."""
    packages = (
        _package("geofabrik:europe/germany", GERMANY),
        _package("geofabrik:europe/germany/bayern", BAVARIA),
        _package("geofabrik:europe/netherlands", NETHERLANDS),
    )

    forwards = select_coverage(MUNICH, packages)
    backwards = select_coverage(MUNICH, tuple(reversed(packages)))

    assert forwards == backwards


# --- Helpers -----------------------------------------------------------------


def _attribution(**overrides: object) -> MapAttribution:
    """Return a usable attribution, with fields replaced for one test."""
    values: dict[str, object] = {
        "data_owner": "OpenStreetMap contributors",
        "provider": "Geofabrik GmbH",
        "license_identifier": "ODbL-1.0",
        "license_name": "Open Database License 1.0",
        "required_text": "Map data © OpenStreetMap contributors",
        "links": (),
    }
    values.update(overrides)
    return MapAttribution(**values)  # type: ignore[arg-type]


def _package(
    identity: str,
    bounds: tuple[float, float, float, float],
    *,
    digest: str = "a" * 64,
    provider: str | None = None,
    downloaded_at: datetime | None = None,
) -> MapPackage:
    """Return an installed package covering ``bounds``."""
    region = MapRegionId.parse(identity)
    west, south, east, north = bounds
    return MapPackage(
        region_id=region,
        region_name=region.path,
        provider=provider if provider is not None else region.provider,
        format=MapPackageFormat.MBTILES,
        tile_schema=MapTileSchema(name="shortbread", version="1.0"),
        content_sha256=digest,
        size_bytes=1024,
        bounds=MapBounds(
            min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
        ),
        min_zoom=0,
        max_zoom=14,
        attribution=_attribution(),
        downloaded_at=downloaded_at or datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        source_url="https://provider.invalid/package.mbtiles",
    )
