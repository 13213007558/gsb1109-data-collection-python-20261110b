from enum import Enum


class UpdateStrategy(str, Enum):  # noqa: UP042 - StrEnum would change str() and format() of members
    """
    Configures when a refreshed recording replaces the cassette file.

    ``REPLACE``: write the new recording straight to the cassette path, the
    historical behavior. A crash mid-write can leave a truncated cassette.

    ``REPLACE_ON_SUCCESS``: write the new recording to a candidate file first
    and move it onto the cassette path only once the whole scenario has
    finished without an error. The previous cassette stays readable until the
    replacement is committed, and a failed refresh leaves its candidate behind
    for debugging instead of corrupting or replacing the working cassette.
    """

    REPLACE = "replace"
    REPLACE_ON_SUCCESS = "replace_on_success"


def validate_update_strategy(update_strategy):
    """Coerce and validate a user-supplied update strategy.

    Accepts either an ``UpdateStrategy`` or one of its string values and
    returns the corresponding ``UpdateStrategy``. Raises ``ValueError`` with a
    helpful message for anything else.
    """
    try:
        return UpdateStrategy(update_strategy)
    except ValueError:
        valid_strategies = ", ".join(repr(strategy.value) for strategy in UpdateStrategy)
        raise ValueError(
            f"{update_strategy!r} is not a valid update_strategy. Valid strategies are: {valid_strategies}.",
        ) from None
