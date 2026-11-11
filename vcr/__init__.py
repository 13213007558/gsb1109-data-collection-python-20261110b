import logging
from logging import NullHandler

from .config import VCR
from .flow import current_flow_group, flow_group
from .record_mode import RecordMode as mode
from .request import Request

__version__ = "9.4.2"

logging.getLogger(__name__).addHandler(NullHandler())


default_vcr = VCR()
use_cassette = default_vcr.use_cassette

__all__ = [
    "VCR",
    "Request",
    "current_flow_group",
    "flow_group",
    "mode",
    "use_cassette",
]
