"""Reading a local file without letting its size decide how much memory it costs.

The import use case stays the single authority on what the limit *is*; this only
makes sure the limit is applied to the read rather than to the verdict. Refusing
an oversized file after loading all of it protects nothing: the cost the limit
exists to prevent has already been paid by then.

This is the reader for paths an operator named on the command line. Those are
trusted input -- somebody typed them -- so an ordinary open is right, and a
symbolic link an operator points at is a file they meant to import. The import
directory is different: nothing there was chosen deliberately, so it reads
through its own boundary (see
:mod:`trackvault.infrastructure.filesystem.import_directory`) and shares only the
margin below.
"""

from pathlib import Path

MAX_READ_MARGIN = 1
"""One byte more than the limit is enough to prove a file is over it."""


def read_bounded(path: Path, max_bytes: int) -> bytes:
    """Return the file's bytes, reading at most one byte past the limit.

    Args:
        path: The file to read. It is opened, never written or moved.
        max_bytes: The import limit this deployment enforces.

    Returns:
        The content, or the first ``max_bytes + 1`` bytes of it. That is enough
        for the import use case -- the authority on the limit -- to refuse the
        file, and it keeps a huge one out of memory.

    Raises:
        OSError: If the file cannot be opened or read. Callers decide what an
            unreadable file means for them; one input path skips it, another
            reports it.
    """
    with path.open("rb") as handle:
        return handle.read(max_bytes + MAX_READ_MARGIN)
