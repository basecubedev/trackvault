"""The extension vocabularies this adapter understands, and nothing more.

GPX defines a small core and then lets a document carry any element from any
namespace. An element *name* therefore says nothing on its own: ``course`` in a
track-point schema is a measured heading, ``course`` in a golf application's
schema is a golf course, and ``activity`` is a word that any vocabulary may use
for any purpose.

Matching a local name in any namespace turns that ambiguity into business
meaning. Measurement evidence is what decides `RECORDED`, so a foreign document
that happens to use the word ``course`` could reach a verdict about data it never
described.

```
(namespace, element)  known   -> business meaning
namespace             unknown -> metadata, and nothing else
```

The table below is small on purpose. It is not a plugin system and not a
catalogue of everything a vendor ever published: it lists the schemas this
project has evidence for, and every entry is exercised by a fixture. A schema
this adapter has never seen data from is unknown data, which parses fine and
means nothing.

This is also the only module that names a source application. That is allowed
here and nowhere else: knowing concrete formats is what an infrastructure adapter
is *for*, while the domain, the application layer and the API stay
source-agnostic -- enforced by ``tests/contract/test_architecture_contract.py``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from trackvault.infrastructure.gpx.parsing import GPX_1_0_NAMESPACE

GARMIN_TRACK_POINT_EXTENSION_V2 = "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"
"""The track-point schema that defines a measured heading, and what a body did.

Version 1 of the same family defines temperature, heart rate and cadence but no
heading, so it is deliberately not listed: supporting "every version that ever
existed" would be exactly the blind vendor matching this module replaces. A
document carrying v1 readings is not misread here -- it is unknown data, which
parses fine and means nothing, and listing v1 is a change to make when a fixture
in this project carries one.
"""

LOCUS_MAP_EXTENSIONS = "https://www.locusmap.app"
"""The recording application's own namespace, as its exports declare it.

Only this spelling is listed. Earlier releases of the same application used other
namespace URIs, and claiming to read a schema no sample in this project uses
would be a promise nothing here can keep.
"""


@dataclass(frozen=True, slots=True)
class ExtensionSchema:
    """One extension vocabulary, and the elements it is read for.

    Attributes:
        name: Short identifier of the schema, for diagnostics and documentation.
        namespaces: The namespace URIs that identify it.
        activity_elements: Local names stating an activity explicitly.
        course_elements: Local names carrying a measured heading.
        navigation_elements: Local names carrying a turn-by-turn instruction.
        heart_rate_elements: Local names carrying a measured heart rate.
        cadence_elements: Local names carrying a measured cadence.
    """

    name: str
    namespaces: frozenset[str]
    activity_elements: frozenset[str] = field(default_factory=frozenset)
    course_elements: frozenset[str] = field(default_factory=frozenset)
    navigation_elements: frozenset[str] = field(default_factory=frozenset)
    heart_rate_elements: frozenset[str] = field(default_factory=frozenset)
    cadence_elements: frozenset[str] = field(default_factory=frozenset)


KNOWN_SCHEMAS: tuple[ExtensionSchema, ...] = (
    ExtensionSchema(
        name="gpx-1.0-core",
        namespaces=frozenset({GPX_1_0_NAMESPACE}),
        # GPX 1.0 puts the heading on the position itself. GPX 1.1 dropped the
        # element, which is why writers moved it into an extension.
        course_elements=frozenset({"course"}),
    ),
    ExtensionSchema(
        name="garmin-trackpoint-v2",
        namespaces=frozenset({GARMIN_TRACK_POINT_EXTENSION_V2}),
        course_elements=frozenset({"course"}),
        heart_rate_elements=frozenset({"hr"}),
        cadence_elements=frozenset({"cad"}),
    ),
    ExtensionSchema(
        name="locus-map",
        namespaces=frozenset({LOCUS_MAP_EXTENSIONS}),
        activity_elements=frozenset({"activity"}),
        navigation_elements=frozenset({"rtePointAction"}),
    ),
)

RECEIVER_QUALITY_ELEMENTS: tuple[str, ...] = ("hdop", "vdop", "pdop", "sat", "fix")
"""How well the receiver was doing, as GPX itself defines it.

These are core GPX elements, so they are read from the document's own GPX
namespace and as direct children of a position -- which is the only place the
schema allows them. An extension namespace that reuses one of the names has not
described a receiver.
"""


def _qualified(elements: Callable[[ExtensionSchema], frozenset[str]]) -> frozenset[tuple[str, str]]:
    """Return the (namespace, element) pairs one kind of reading covers."""
    return frozenset(
        (namespace, element)
        for schema in KNOWN_SCHEMAS
        for namespace in schema.namespaces
        for element in elements(schema)
    )


ACTIVITY_ELEMENTS = _qualified(lambda schema: schema.activity_elements)
"""Where an explicitly stated activity may be read from."""

COURSE_ELEMENTS = _qualified(lambda schema: schema.course_elements)
"""Where a measured heading may be read from."""

NAVIGATION_ELEMENTS = _qualified(lambda schema: schema.navigation_elements)
"""Where a turn-by-turn navigation instruction may be read from."""

HEART_RATE_ELEMENTS = _qualified(lambda schema: schema.heart_rate_elements)
"""Where a measured heart rate may be read from.

`hr` is two letters and any vocabulary may use them. Reading a body measurement
out of an element only because it is spelled that way is exactly the blind
matching this module exists to prevent, so the namespace decides here too.
"""

CADENCE_ELEMENTS = _qualified(lambda schema: schema.cadence_elements)
"""Where a measured cadence may be read from."""
