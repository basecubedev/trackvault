"""How a downloadable map region is named, and why the name is never a path.

A region arrives from a provider's catalog, which is remote, untrusted input.
Two separate things have to be true about it:

- it identifies one region unambiguously, across providers and over time, so a
  correction, an update and a coverage decision all mean the same region;
- it can never decide where a byte lands on the file system.

The first is why the identity is provider-scoped rather than a display name:
"Limburg" is a Dutch province and a Belgian one, and "Georgia" is a country and
a US state. The second is why the managed storage key is a *hash* of the
identity rather than the identity with its separators swapped. A validation
defect in the parser above would then still not be expressible as a path -- a
hexadecimal digest has no `..` to write.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Self

PROVIDER_PATTERN = re.compile(r"\A[a-z][a-z0-9-]{1,31}\Z")
"""What a provider slug may look like. Lower case, no separators of any kind."""

SEGMENT_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
"""What one path segment of a region may look like.

Deliberately excludes `.`, which is what makes `..` unwriteable rather than
merely rejected: there is no character in the class that could form it.
"""

MAX_SEGMENTS = 6
"""How deep a region hierarchy may go. Geofabrik's deepest today is four."""

IDENTITY_SEPARATOR = ":"
SEGMENT_SEPARATOR = "/"


@dataclass(frozen=True, slots=True)
class MapRegionId:
    """A provider-scoped region identity, for example ``geofabrik:europe/monaco``.

    Attributes:
        provider: The catalog this region belongs to.
        segments: The region path inside that catalog, outermost first.
    """

    provider: str
    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse anything that is not a well-formed region identity."""
        if not PROVIDER_PATTERN.fullmatch(self.provider):
            raise ValueError("map region provider is not a valid slug")
        if not 1 <= len(self.segments) <= MAX_SEGMENTS:
            raise ValueError(f"a map region has 1..{MAX_SEGMENTS} path segments")
        for segment in self.segments:
            if not SEGMENT_PATTERN.fullmatch(segment):
                raise ValueError("map region path segment is not a valid slug")

    @classmethod
    def parse(cls, value: str) -> Self:
        """Return the identity a caller's string names.

        Args:
            value: A ``provider:path/to/region`` string.

        Returns:
            The parsed identity.

        Raises:
            ValueError: If the string is not a well-formed region identity. The
                message names the shape and never echoes the input, which would
                make the error a reflection surface.
        """
        provider, separator, path = value.partition(IDENTITY_SEPARATOR)
        if not separator:
            raise ValueError("a map region identity is 'provider:path'")
        return cls(provider=provider, segments=tuple(path.split(SEGMENT_SEPARATOR)))

    @property
    def path(self) -> str:
        """Return the region path inside its provider's catalog."""
        return SEGMENT_SEPARATOR.join(self.segments)

    @property
    def parent(self) -> Self | None:
        """Return the region one level up, or ``None`` at the catalog root."""
        if len(self.segments) == 1:
            return None
        return type(self)(provider=self.provider, segments=self.segments[:-1])

    @property
    def storage_key(self) -> str:
        """Return the managed directory name this region's package lives under.

        A digest rather than a transformation of the identity. The identity is
        already validated to characters that cannot form a path, so this is the
        second of two independent reasons a provider cannot reach outside the
        managed root -- and the one that still holds if the first is wrong.
        """
        return hashlib.sha256(str(self).encode("utf-8")).hexdigest()

    def __str__(self) -> str:
        """Return the canonical ``provider:path`` form."""
        return f"{self.provider}{IDENTITY_SEPARATOR}{self.path}"
