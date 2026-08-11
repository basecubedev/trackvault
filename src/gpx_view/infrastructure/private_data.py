"""How GPX-View creates the files that hold private movement data.

A GPS archive is a movement profile of a real person: where they live, when they
are away, which route they take every morning. On a self-hosted machine it sits
beside other users' home directories and other services' data, so the archive
creates its own artifacts readable by their owner and by nobody else.

```
directories GPX-View creates   0700
files GPX-View creates         0600
```

Two properties matter as much as the numbers.

**The mode is set at creation.** Writing a file and tightening it afterwards
leaves a window in which it was world-readable, and a window is all an unrelated
process on the same machine needs. Every creation here states its mode to the
system call that creates the object.

**The umask cannot widen it.** A umask only ever removes permission bits, so
requesting 0600 comes out at most 0600 whatever the operator's login shell was
configured with. The usual 0o666 or 0o777 request would come out world-readable
under the ordinary umask 022, which is exactly the default this module refuses.

**Nothing existing is chmodded.** A path inside the data directory may carry an
operator's own access decision -- a shared group, an ACL, a backup agent -- and
silently overwriting that would replace one surprise with a worse one. GPX-View
decides the permissions of what it creates and reports, rather than repairs, what
it finds.
"""

import os
from contextlib import suppress
from pathlib import Path

PRIVATE_FILE_MODE = 0o600
"""Readable and writable by the owning user only."""

PRIVATE_DIRECTORY_MODE = 0o700
"""Listable and traversable by the owning user only."""


def create_private_directory(path: Path) -> None:
    """Create a directory, private from creation, and leave an existing one alone.

    Missing intermediate parents are created with the process default rather than
    with this mode -- that is what ``Path.mkdir`` does, and it is the right
    outcome here: the directory GPX-View owns is the leaf, and an empty
    intermediate directory holds nothing to protect.
    """
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)


def create_private_file(path: Path) -> None:
    """Make sure a file exists and, if GPX-View created it, that it is private.

    Used where a library would otherwise create the file itself with its own
    default mode. Claiming the name first is what removes the window: there is no
    moment at which the file exists and is readable by anyone else.

    An existing file is left exactly as it is, including its mode.
    """
    with suppress(FileExistsError):
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, PRIVATE_FILE_MODE))
