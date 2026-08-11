"""Normalizing an explicitly stated activity onto the domain vocabulary.

GPX states an activity in ``<trk><type>`` and, depending on the writing
application, in an ``activity`` element inside ``<extensions>``. Both are
*explicit activity metadata*, so normalizing them is not a source shortcut: the
source said "walking", and we record walking.

The table below maps activity words, not applications. There is no entry for a
vendor, and nothing here influences a track kind.
"""

from trackvault.domain import Activity

_SYNONYMS: dict[str, Activity] = {
    "walking": Activity.WALKING,
    "walk": Activity.WALKING,
    "foot": Activity.WALKING,
    "on_foot": Activity.WALKING,
    "hiking": Activity.HIKING,
    "hike": Activity.HIKING,
    "trekking": Activity.HIKING,
    "cycling": Activity.CYCLING,
    "cycle": Activity.CYCLING,
    "bike": Activity.CYCLING,
    "biking": Activity.CYCLING,
    "bicycle": Activity.CYCLING,
    "running": Activity.RUNNING,
    "run": Activity.RUNNING,
    "jogging": Activity.RUNNING,
    "scooter": Activity.SCOOTER,
    "kick_scooter": Activity.SCOOTER,
    "motorcycle": Activity.MOTORCYCLE,
    "motorbike": Activity.MOTORCYCLE,
    "motorcycling": Activity.MOTORCYCLE,
    "other": Activity.OTHER,
}


def normalize_activity(*candidates: str | None) -> Activity:
    """Return the first candidate that maps onto a known activity.

    Args:
        candidates: Activity strings the document stated, in the order they
            should be trusted.

    Returns:
        The mapped activity, or ``Activity.UNKNOWN`` when the source stated
        nothing we recognise. An unrecognised value never becomes a guess.
    """
    for candidate in candidates:
        if candidate is None:
            continue
        key = candidate.strip().lower().replace(" ", "_").replace("-", "_")
        if activity := _SYNONYMS.get(key):
            return activity
    return Activity.UNKNOWN


def states_an_activity(*candidates: str | None) -> bool:
    """Report whether the source stated an activity at all, mappable or not."""
    return any(candidate and candidate.strip() for candidate in candidates)
