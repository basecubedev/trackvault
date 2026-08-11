"""Which installed packages should be drawn behind a given rectangle.

```
Germany and Bavaria installed, track inside Bavaria     ->  Bavaria
only Germany installed, track inside Bavaria            ->  Germany
Germany and Bavaria installed, track spans two states   ->  Germany
Germany and the Netherlands, track crosses the border   ->  both
nothing installed for the area                          ->  nothing
```

Two facts decide all five, and it matters that they are different kinds of fact.

**Whether a package covers the rectangle** is geometry: four float comparisons
against the package's own extent.

**Whether one package supersedes another** is *hierarchy*, taken from the
region identity the provider published -- not from the rectangles. That
distinction is the whole design. Germany's bounding box reaches well into the
Netherlands, so a rectangle near Aachen is "covered" by both countries and
geometry alone cannot tell that only one of them holds data there. The region
tree can: Bavaria is inside Germany because the provider says
`europe/germany/bayern` is inside `europe/germany`, and the Netherlands is not.

So a parent is dropped only when a *descendant of it* covers the rectangle on
its own, and two neighbours are both kept. That is what stops Germany and
Bavaria drawing the same street twice -- two sources over one area collide
labels and darken every fill, which looks like a rendering fault rather than
like a selection mistake -- while a track that leaves one country keeps a
basemap on the other side.

Nothing is also a normal answer. A track in a country whose map is not
installed still draws, over a neutral background, with a sentence saying why.
"""

from collections.abc import Iterable, Sequence

from trackvault.domain.maps.bounds import MapBounds
from trackvault.domain.maps.identity import MapRegionId
from trackvault.domain.maps.package import MapPackage


def select_coverage(bounds: MapBounds, packages: Iterable[MapPackage]) -> tuple[MapPackage, ...]:
    """Return the packages to draw behind ``bounds``, most specific first.

    Args:
        bounds: The rectangle that has to be covered -- a track's extent, or a
            viewport around it.
        packages: The installed packages, in any order.

    Returns:
        The packages to stack, most specific first, or an empty tuple when
        nothing intersects.
    """
    candidates = [package for package in packages if package.bounds.intersects(bounds)]
    if not candidates:
        return ()

    covering = [package for package in candidates if package.bounds.covers(bounds)]
    # A package that covers the rectangle by itself makes every package it
    # contains unnecessary *and* every package it does not reach irrelevant, so
    # the covering set is the whole answer whenever it is not empty.
    return _by_specificity(_without_superseded(covering or candidates))


def _without_superseded(packages: Sequence[MapPackage]) -> list[MapPackage]:
    """Drop every package that a more specific one in the same tree replaces."""
    return [
        package
        for package in packages
        if not any(
            other is not package and _is_inside(other.region_id, package.region_id)
            for other in packages
        )
    ]


def _is_inside(candidate: MapRegionId, ancestor: MapRegionId) -> bool:
    """Report whether one region is a descendant of another in its catalog.

    Provider-scoped: two catalogs may both hold a `europe/germany`, and one
    saying so about the other would be a coincidence of naming rather than a
    statement about the data.
    """
    if candidate.provider != ancestor.provider:
        return False
    depth = len(ancestor.segments)
    return len(candidate.segments) > depth and candidate.segments[:depth] == ancestor.segments


def _by_specificity(packages: Sequence[MapPackage]) -> tuple[MapPackage, ...]:
    """Return packages ordered smallest-extent first, then by identity.

    The identity tie-breaker is not decoration. Two packages of identical extent
    would otherwise come out in whatever order the repository happened to
    return, and a basemap that changes between two page loads for no reason is a
    bug report nobody can reproduce.
    """
    return tuple(
        sorted(packages, key=lambda package: (package.bounds.area, str(package.region_id)))
    )
