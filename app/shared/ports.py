"""Shared application-layer ports (dependency-inversion seams).

`InUseChecker` answers "is this entity still referenced by something that should
block its deletion?" It is wired at the API layer (composition root) so a service
never imports another module's infrastructure to run the check.
"""

from collections.abc import Awaitable, Callable

InUseChecker = Callable[[int], Awaitable[bool]]
