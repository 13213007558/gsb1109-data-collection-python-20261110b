import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .request import Request


class UnusedInteractionReason(str, enum.Enum):  # noqa: UP042 - StrEnum would change str() and format() of members
    """Why a recorded interaction was never consumed during playback.

    ``NEVER_PLAYED``: no playback request consumed it. Either no request with
    the same method and URI was made at all (the code branch that performs it
    never ran), or fewer requests were made than there are duplicate
    recordings for that address.

    ``BODY_CHANGED``: a request with the same method and URI was made, but it
    no longer matches this interaction under the cassette's matchers, e.g. the
    request body changed since the cassette was recorded.
    """

    NEVER_PLAYED = "never_played"
    BODY_CHANGED = "body_changed"


@dataclass(frozen=True, slots=True)
class UnusedInteraction:
    """A recorded interaction that no playback request consumed.

    Returned by ``Cassette.unused_interactions`` for tidying up old cassettes.
    Auditing is read-only: it never plays, rewrites or removes anything.

    - ``index``: the interaction's position in the recording (its index in
      ``Cassette.data``, zero-based).
    - ``request``/``response``: the recorded request and response.
    - ``reason``: an :class:`UnusedInteractionReason`.
    - ``detail``: a human-readable explanation of the reason.
    """

    index: int
    request: "Request"
    response: object
    reason: UnusedInteractionReason
    detail: str
