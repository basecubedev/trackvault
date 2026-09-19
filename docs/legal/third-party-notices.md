# Third-party notices

The canonical list of what TrackVault depends on, what each dependency is for, and
what licence it arrived under. There is exactly one of these: `README.md`, the
`Dockerfile` and the ADRs point here rather than repeating it, because three
copies of a dependency list are three lists and only one of them is right.

> This is engineering documentation, not legal advice. It records what the
> installed packages declare and what this repository has decided to accept. It
> does not certify that any use is lawful, and nothing here should be read as
> claiming it does.

## How this list is kept honest

Two audits read the **resolved** trees rather than the intent files, because
what ends up in a container is what was resolved and not what somebody asked
for:

```bash
uv run python scripts/audit_licenses.py     # every installed Python distribution
cd web && npm ci && npm run licenses        # every package package-lock.json resolves
```

Both read one `license-policy.json` at the repository root, so a licence
decision is made once for the repository rather than once per ecosystem. Both
exit non-zero when a package's licence is not one the policy already accepts, so
a dependency that arrives with a surprise is noticed when it arrives.

Three outcomes, and the middle one is the point:

| Outcome | Meaning |
| --- | --- |
| allowed | the licence is on the repository's allowlist |
| review required | a real licence that nobody here has read yet |
| unknown | the package states no licence at all |

"Review required" is not "forbidden". It is the honest report that a human has
to look at something, and adding the identifier to the allowlist is what reading
it results in. The allowlist is short on purpose: it holds the licences the
dependencies this project actually needs arrived under, not every permissive
licence in existence.

Individual decisions that are narrower than a licence live in the policy's
`reviewed` map. Today that is one entry:

| Package | Reviewed licence | Decision |
| --- | --- | --- |
| `pathspec` | MPL-2.0 | Build-time dependency of the linter, used unmodified and never shipped in the runtime image. MPL's file-level copyleft reaches modified MPL files, of which this repository has none. |

A review names the **licence** it was granted for, not only the package. What
somebody read was a document, so an exception recorded against the name alone
would outlive it: `pathspec` relicensed tomorrow would keep passing a gate under
a decision nobody ever made about it. Both audits therefore apply an exception
only while the package still declares the reviewed identifier, and report
anything else as `relicensed` — a third outcome, distinct from "never reviewed",
because it needs a different conversation.

## How the tables below are kept honest

The tables are written by hand and would otherwise drift the moment a
dependency is bumped: the lock file moves, the document keeps stating last
month's version, and nothing about the result looks wrong.
`tests/contract/test_third_party_notice_contract.py` closes that. For every
**direct** dependency of either ecosystem it checks that

```
the package is documented, in the runtime or the development table it belongs to
the documented version is the version the lock file resolved
the documented licence is the licence the package itself declares
the row states a purpose
```

and that the tables document nothing that is not a direct dependency any more.
Transitive dependencies are deliberately absent from this document — several
hundred rows nobody reads is a worse statement than none — and stay covered by
the two audits above, which read the whole resolved trees.

The first column is the **package identifier**, not a product name, so a reader
and a test are looking at the same string.

## What ships, and what only builds

The distinction that matters for a self-hosted deployment:

```
runtime      in the container, executing when somebody uses the archive
build        needed to produce the container, absent from it
development  needed to work on the project, absent from CI images and containers
```

The final image carries the Python runtime dependencies, the built browser
assets and a copy of this document at `/app/THIRD_PARTY_NOTICES.md`. It carries
no Node.js, no `node_modules`, no test tooling and no linter. Whoever runs the
container is who these statements are for, so they travel with it rather than
staying in a repository that operator may never see.

## Python — runtime

| Package | Version | Purpose | Licence | Upstream |
| --- | --- | --- | --- | --- |
| `fastapi` | 0.141.1 | FastAPI, the HTTP framework the API is projected through | MIT | <https://github.com/fastapi/fastapi> |
| `pydantic` | 2.13.4 | Request and response validation | MIT | <https://github.com/pydantic/pydantic> |
| `pydantic-settings` | 2.15.0 | Reading `TRACKVAULT_*` configuration | MIT | <https://github.com/pydantic/pydantic-settings> |
| `uvicorn` | 0.52.1 | ASGI server | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| `defusedxml` | 0.7.1 | Hardened XML parsing for untrusted GPX documents | Python-2.0 | <https://github.com/tiran/defusedxml> |

Their own transitive dependencies -- Starlette, `anyio`, `h11`, `click`,
`typing-extensions` and the rest -- are covered by the audit above and are MIT,
BSD, Apache-2.0, PSF-2.0 or a choice including one of those.

## Python — development and test

| Package | Version | Purpose | Licence |
| --- | --- | --- | --- |
| `pytest` | 9.1.1 | Test runner | MIT |
| `pytest-cov` | 7.1.0 | Coverage reporting | MIT |
| `ruff` | 0.16.2 | Linter and formatter | MIT |
| `mypy` | 2.3.0 | Type checker | MIT |
| `httpx2` | 2.13.0 | HTTP client for the Docker smoke tests | BSD-3-Clause |
| `types-defusedxml` | 0.7.0.20260504 | Type stubs for `defusedxml` | Apache-2.0 |
| `pyyaml` | 6.0.3 | Reading the workflow files in the release contract test | MIT |

## Browser — runtime

Everything here is bundled into the assets the container serves.

| Package | Version | Purpose | Licence | Upstream |
| --- | --- | --- | --- | --- |
| `react` | 19.2.0 | User interface | MIT | <https://github.com/facebook/react> |
| `react-dom` | 19.2.0 | Browser renderer for React | MIT | <https://github.com/facebook/react> |
| `react-router-dom` | 7.18.4 | Client-side routing | MIT | <https://github.com/remix-run/react-router> |
| `maplibre-gl` | 5.9.0 | MapLibre GL JS, the interactive track map | BSD-3-Clause | <https://github.com/maplibre/maplibre-gl-js> |
| `echarts` | 5.6.0 | Apache ECharts: monthly chart and track profiles | Apache-2.0 | <https://github.com/apache/echarts> |

## Browser — build and test

| Package | Version | Purpose | Licence |
| --- | --- | --- | --- |
| `vite` | 7.3.6 | Bundler and development server | MIT |
| `@vitejs/plugin-react` | 5.0.4 | React support for Vite | MIT |
| `typescript` | 5.9.3 | Type checker | Apache-2.0 |
| `typescript-eslint` | 8.66.0 | Type-aware linting | MIT |
| `eslint` | 9.39.0 | Linter | MIT |
| `@eslint/js` | 9.39.0 | ESLint's own recommended rules | MIT |
| `eslint-plugin-react-hooks` | 6.1.1 | Hook rules | MIT |
| `globals` | 16.5.0 | Environment globals for the linter | MIT |
| `vitest` | 3.2.7 | Unit and component test runner | MIT |
| `jsdom` | 27.0.0 | Browser environment for component tests | MIT |
| `fake-indexeddb` | 6.2.5 | The one browser API `jsdom` does not implement, for the minimap cache tests | Apache-2.0 |
| `@testing-library/react` | 16.3.0 | Component testing | MIT |
| `@testing-library/user-event` | 14.6.1 | Simulating real user interaction in component tests | MIT |
| `@testing-library/jest-dom` | 6.9.1 | Assertions about rendered markup | MIT |
| `@playwright/test` | 1.57.0 | Browser end-to-end tests | Apache-2.0 |
| `openapi-typescript` | 7.13.0 | Generates the API types from the backend schema | MIT |
| `@types/node` | 26.2.0 | Type definitions for the Node build scripts | MIT |
| `@types/react` | 19.2.2 | Type definitions for React | MIT |
| `@types/react-dom` | 19.2.1 | Type definitions for React DOM | MIT |

## Map data, tiles and fonts

TrackVault ships **no map data in its image**. A regional map is a package an
operator installs into their own data directory, and everything about it --
where it came from, under what licence, and what has to be shown while it
renders -- is read out of the package and stored beside it. See
`docs/adr/0010-offline-map-packages-and-local-basemap-delivery.md`.

### The data an installed package carries

| | |
| --- | --- |
| Data | OpenStreetMap, © OpenStreetMap contributors |
| Data licence | Open Database License 1.0 (ODbL-1.0) |
| Packaged by | Geofabrik GmbH, from its public download service |
| Tile schema | [Shortbread](https://shortbread-tiles.org/) 1.0, schema documentation CC0-1.0 |
| Where it comes from | <https://download.geofabrik.de/> |

Three consequences an operator has to know about.

**Attribution is a requirement, not a decoration.** ODbL-1.0 requires that the
data's source be credited wherever it is publicly used. TrackVault reads the
attribution out of the package's own metadata and renders it beside every map
it draws, on the track page, in the map manager and on `/credits`. It cannot be
switched off, and a package that states no author and no licence is refused
rather than installed.

**A data licence is not a software licence.** ODbL-1.0 and CC0-1.0 appear here
and deliberately **not** in `license-policy.json`: that file gates the licences
of *code* this repository depends on, and mixing a database licence into a
software allowlist would make both statements harder to read. This section is
where the data decision is recorded.

**Redistributing a package is the operator's decision, not this project's.**
TrackVault downloads a package into one deployment for that deployment's use. An
operator who then publishes those tiles onwards is making an ODbL decision of
their own, and this document does not make it for them.

### Fonts

Map labels are drawn from signed-distance-field glyph ranges under
`web/public/fonts/`, generated by `scripts/generate_glyphs.py`.

| | |
| --- | --- |
| Font | Noto Sans (Regular and Bold) |
| Licence | SIL Open Font License 1.1 |
| Upstream | <https://notofonts.github.io/> |
| What is shipped | Derived SDF glyph ranges, not the font files themselves |

The OFL permits redistribution of the font and of derived works, including
embedded and rasterised forms, provided the licence and copyright notice travel
with them -- which this entry is. Noto Sans carries no Reserved Font Name, so
the font stack keeps its own name.

No sprite sheet is shipped. Neither of the two themes uses `icon-image`, so
there is no icon set to license and nothing to load.

### What is *not* used

- **No public tile service.** `tile.openstreetmap.org` and equivalents are
  community infrastructure whose usage policies exist to prevent exactly the
  bulk download an offline archive would need. They are not used, and they are
  not an offline-download authority.
- **No commercial provider, no API key, no account.** Normal use of TrackVault
  needs none.
- **No CDN.** Scripts, styles, fonts, sprites and tiles are all served by the
  deployment itself, and the content security policy says `default-src 'self'`
  so a browser refuses anything else.

## TrackVault itself

TrackVault is licensed under the **GNU Affero General Public License, version 3
only** (`AGPL-3.0-only`). The full text is in [`LICENSE`](../../LICENSE) at the
repository root, and it ships inside the container image at `/app/LICENSE` --
conveying the program means conveying its licence, and a licence that stays in a
Git tree the operator never sees is not one.

The identifier is stated in exactly three machine-readable places, all derived
from that one file:

| Where | What it says |
| --- | --- |
| `pyproject.toml` | `license = "AGPL-3.0-only"`, `license-files = ["LICENSE"]` |
| `web/package.json` | `"license": "AGPL-3.0-only"` |
| the image | `org.opencontainers.image.licenses=AGPL-3.0-only` |

AGPL-3.0-only is deliberately **not** on the `allowed` list in
`license-policy.json`. That list is what this project accepts *from a
dependency*, and the two questions are unrelated: a copyleft licence this
project chose for its own code says nothing about which licences it is willing
to take somebody else's code under. The project's own distributions are named in
the policy's `self` list and excluded from both audits, because auditing a
project against a policy it wrote says nothing.

Everything in the tables above keeps its own licence, unchanged. So does the map
data (ODbL) and so do the fonts (OFL) -- see the sections above. Nothing here
relicenses anybody else's work.
