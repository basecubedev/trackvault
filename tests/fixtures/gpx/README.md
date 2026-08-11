# GPX import fixtures

Every file here is **synthetic**. No coordinate, timestamp, name or identifier
comes from a real recording — see `docs/developer/agent-rules.md`, section
"Personal GPS data". Personal GPX files are never committed, not even trimmed.

The files stay deliberately small: each proves one contract, and a fixture large
enough to be slow is a fixture nobody reads.

| File | Proves |
| --- | --- |
| `recorded-measurements.gpx` | receiver quality and heading extensions, activity from `<type>`, export time later than the track |
| `ambiguous-minimal.gpx` | positions and timestamps only — not decidable |
| `planned-route-instructions.gpx` | waypoints carrying turn-by-turn navigation instructions |
| `ambiguous-measured-with-instructions.gpx` | measurements *and* navigation instructions — not decidable |
| `multiple-tracks.gpx` | one file, several `<trk>` elements, several tracks |
| `multiple-segments.gpx` | one `<trk>`, several `<trkseg>` — one track, boundaries kept |
| `missing-timestamps.gpx` | a track without any `<time>` |
| `missing-elevation.gpx` | a track without any `<ele>` |
| `activity-extension.gpx` | an explicit activity in a known extension namespace |
| `unknown-extension.gpx` | an unknown extension namespace must not fail the parse |
| `unknown-namespace-measurements.gpx` | `hdop`, `sat`, `fix` and `course` in a foreign namespace are not measurements |
| `unknown-namespace-activity.gpx` | an `activity` element in a foreign namespace is not an activity |
| `unknown-namespace-instructions.gpx` | a navigation-looking element in a foreign namespace is not planning evidence |
| `unregistered-trackpoint-schema.gpx` | a track-point schema version this project has no evidence for is unknown data |
| `long-prolog.gpx` | more than four kilobytes of comments before the root element |
| `utf-8-bom.gpx` | a byte order mark in front of the XML declaration |
| `utf-16.gpx` | a document that declares, and is written in, UTF-16 |
| `route-only.gpx` | a `<rte>` element produces a track candidate |
| `generic-external-link.gpx` | an ordinary external `<link>` is provenance, not proof that the geometry was planned |
| `track-beside-navigated-route.gpx` | one document, a plain `<trk>` and a `<rte>` carrying navigation instructions — evidence stays with its own candidate |
| `prefixed-root.gpx` | `<g:gpx xmlns:g="...">` is the same element as `<gpx xmlns="...">` |
| `empty-track.gpx` | a `<trk>` without geometry yields no candidate |
| `gpx-1.0.gpx` | GPX 1.0: metadata in the root, `<course>` and `<url>` as direct children |
| `invalid-coordinate.gpx` | a latitude outside the valid range is refused |
| `missing-coordinate.gpx` | a point without a longitude is refused |
| `non-numeric-coordinate.gpx` | a latitude that is not a number is refused |
| `non-numeric-elevation.gpx` | an elevation that is not a number is refused |
| `non-finite-elevation.gpx` | `NaN` must not be stored as a number |
| `wrong-root-element.gpx` | GPX namespace, wrong root element |
| `invalid-timestamp.gpx` | an unparseable `<time>` is refused |
| `naive-timestamp.gpx` | a timestamp without a zone is ambiguous and refused |
| `malformed.gpx` | claims to be GPX, is not well-formed XML |
| `unsafe-entity.gpx` | an internal DTD entity definition must be refused, not expanded |
| `unsafe-external-entity.gpx` | an external entity reference must be refused, and never fetched |
| `not-gpx.xml` | well-formed XML that is not GPX |
| `not-xml.txt` | not XML at all |
