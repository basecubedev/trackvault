"""Stable evidence codes an importer may observe about a track.

An importer states *what it saw*; it never states what that means. The classifier
weighs these observations and is the only place that turns them into a
:class:`~gpx_view.domain.track_kind.TrackKind`.

The codes are part of the stored classification result and therefore part of the
data contract: they are lower case, underscore separated, and they are not renamed
between parser versions. Adding a code is cheap, changing the meaning of one
invalidates every stored explanation.

That is why a code whose *meaning* turns out to be wrong is deprecated rather
than redefined. It stops being produced, keeps the meaning it had, and a new code
states the observation correctly. Stored results name the classifier version that
wrote them, so an old explanation stays readable, and reprocessing replaces it.
"""

from collections.abc import Iterable
from enum import StrEnum


class EvidenceCode(StrEnum):
    """One neutral observation about a normalized track.

    Attributes:
        TRACK_ELEMENT_PRESENT: The source described this track as a recorded
            track structure.
        ROUTE_ELEMENT_PRESENT: The source described this track as a planned route
            structure.
        ROUTE_INSTRUCTIONS_PRESENT: The document carries turn-by-turn navigation
            instructions. Instructions exist because a route was computed for
            them; a recording has nowhere to get them from.
        TIMESTAMPS_PRESENT: At least one position carries an instant.
        TIMESTAMPS_ABSENT: No position carries an instant.
        GPS_ACCURACY_PRESENT: Positions carry receiver-quality values such as a
            dilution of precision, a satellite count or a fix type. A device that
            measures reports how well it measured; a planner has nothing to report.
        COURSE_MEASUREMENTS_PRESENT: Positions carry a measured heading.
        MEASUREMENT_METADATA_ABSENT: No position carries any measurement metadata
            at all -- neither receiver quality nor heading.
        SOURCE_LINK_PRESENT: Deprecated, and no longer produced by any importer.
            It claimed the document declared where its geometry *came from*, which
            is more than an exchange-format link says. Stored results from
            classifier version 1 still cite it, and it keeps its old meaning there,
            so those explanations stay readable. Reprocessing replaces it.
        EXTERNAL_LINK_PRESENT: The document declares a link to an external web
            resource. What that resource is, and whether the geometry came from
            it, is not observable -- an application writing its own home page and
            a planner writing a permalink produce the same observation.
        ACTIVITY_METADATA_PRESENT: The source states an activity explicitly.
    """

    TRACK_ELEMENT_PRESENT = "track_element_present"
    ROUTE_ELEMENT_PRESENT = "route_element_present"
    ROUTE_INSTRUCTIONS_PRESENT = "route_instructions_present"
    TIMESTAMPS_PRESENT = "timestamps_present"
    TIMESTAMPS_ABSENT = "timestamps_absent"
    GPS_ACCURACY_PRESENT = "gps_accuracy_present"
    COURSE_MEASUREMENTS_PRESENT = "course_measurements_present"
    MEASUREMENT_METADATA_ABSENT = "measurement_metadata_absent"
    SOURCE_LINK_PRESENT = "source_link_present"
    EXTERNAL_LINK_PRESENT = "external_link_present"
    ACTIVITY_METADATA_PRESENT = "activity_metadata_present"


def in_canonical_order(codes: Iterable[EvidenceCode]) -> tuple[EvidenceCode, ...]:
    """Return the given codes deduplicated and in declaration order.

    Evidence is compared, stored and rendered, so its order must not depend on the
    order in which a parser happened to notice things.
    """
    unique = set(codes)
    return tuple(code for code in EvidenceCode if code in unique)
