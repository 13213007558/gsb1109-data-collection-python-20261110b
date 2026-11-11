"""Recorded request failures.

A cassette interaction usually holds a complete response. It can instead hold
a *failure*: an exception that interrupted an HTTP interaction, either before
any response was received or while the response body was being delivered.

A failure is a plain, JSON/YAML-safe dict so it is stored in the cassette like
the rest of the interaction::

    {"type": "http.client.RemoteDisconnected",
     "message": "Remote end closed connection without response",
     "phase": "before_response"}

``phase`` is one of:

- ``before_response``: the request failed before the response started; replay
  raises the exception instead of returning a response.
- ``during_body``: the response line and headers arrived, part of the body was
  delivered, and then reading failed. The partial body is stored normally and
  replayed up to its end, at which point the exception is raised.
"""

import importlib
import logging

log = logging.getLogger(__name__)

BEFORE_RESPONSE = "before_response"
DURING_BODY = "during_body"
PHASES = (BEFORE_RESPONSE, DURING_BODY)


class PlaybackError(Exception):
    """Replays a recorded failure whose original exception type can't be rebuilt.

    This happens when the recorded class isn't importable at replay time (the
    library that raised it isn't installed) or can't be constructed from the
    recorded message alone. Register a factory with
    :func:`register_playback_exception_factory` to reconstruct such types.
    """

    def __init__(self, failure):
        self.failure = failure
        super().__init__(failure.get("message") or failure.get("type", "recorded failure"))


# Factories for recorded failure types that need more than ``cls(message)`` to
# reconstruct, keyed by the recorded dotted type path.
_EXCEPTION_FACTORIES = {}


def register_playback_exception_factory(type_path, factory):
    """Register ``factory(failure)`` for a recorded failure ``type`` path.

    The factory returns the exception to raise when a failure with that
    ``type`` is replayed. Use it for exception classes that can't be imported
    or constructed from the recorded message alone.
    """
    if not isinstance(type_path, str) or not callable(factory):
        raise TypeError("type_path must be a string and factory must be callable")
    _EXCEPTION_FACTORIES[type_path] = factory


def unregister_playback_exception_factory(type_path):
    """Remove a factory registered with :func:`register_playback_exception_factory`."""
    _EXCEPTION_FACTORIES.pop(type_path, None)


def make_failure(exc, phase, *, message=None, type_path=None):
    """Describe an exception as a cassette failure dict.

    ``phase`` is :data:`BEFORE_RESPONSE` or :data:`DURING_BODY`. The exception
    type is recorded as a dotted import path, so no exception objects have to
    be serialized.
    """
    if phase not in PHASES:
        raise ValueError(f"unknown failure phase {phase!r}, expected one of {PHASES}")
    exception_type = type(exc)
    if type_path is None:
        type_path = f"{exception_type.__module__}.{exception_type.__qualname__}"
    if message is None:
        message = str(exc)
    return {"type": type_path, "message": message, "phase": phase}


def normalize_failure(failure):
    """Validate a failure dict (hand-written or recorded) and fill defaults."""
    if not isinstance(failure, dict):
        raise TypeError(f"failure must be a dict, not {type(failure).__name__}")
    type_path = failure.get("type")
    if not type_path:
        raise ValueError("failure needs a 'type' (the dotted exception class path)")
    phase = failure.get("phase", DURING_BODY)
    if phase not in PHASES:
        raise ValueError(f"unknown failure phase {phase!r}, expected one of {PHASES}")
    message = failure.get("message") or ""
    return {"type": str(type_path), "message": str(message), "phase": phase}


def is_before_response_failure(response):
    """Whether a recorded response dict is a failure before any response arrived."""
    failure = response.get("failure")
    return isinstance(failure, dict) and normalize_failure(failure)["phase"] == BEFORE_RESPONSE


def playback_exception(failure):
    """Build the exception that replays a recorded failure."""
    failure = normalize_failure(failure)
    factory = _EXCEPTION_FACTORIES.get(failure["type"])
    if factory is not None:
        try:
            return factory(failure)
        except Exception:
            log.warning(
                "Registered playback factory for %s failed; falling back to PlaybackError",
                failure["type"],
                exc_info=True,
            )
    exception_class = _import_exception_class(failure["type"])
    if exception_class is not None:
        try:
            return exception_class(failure["message"])
        except Exception:
            log.warning(
                "Could not construct %s from its recorded message; falling back to PlaybackError",
                failure["type"],
                exc_info=True,
            )
    return PlaybackError(failure)


def _import_exception_class(type_path):
    """Resolve a dotted path like ``http.client.RemoteDisconnected`` to a class."""
    module_path, separator, attribute_path = type_path.rpartition(".")
    if not separator:
        return None
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return None
    obj = module
    for attribute in attribute_path.split("."):
        try:
            obj = getattr(obj, attribute)
        except AttributeError:
            return None
    return obj if isinstance(obj, type) and issubclass(obj, BaseException) else None
