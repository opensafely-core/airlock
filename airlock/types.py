from pathlib import PurePosixPath
from typing import TYPE_CHECKING


# We use PurePosixPath as a convenient URL path representation. In theory we could use
# `NewType` here to indicate that we want this to be treated as a distinct type without
# actually creating one. But doing so results in a number of spurious type errors for
# reasons I don't fully understand (possibly because PurePosixPath isn't itself type
# annotated?).
if TYPE_CHECKING:  # pragma: no cover

    class UrlPath(PurePosixPath): ...
else:
    UrlPath = PurePosixPath
