"""Errors raised by the maker core."""


class OpError(Exception):
    """Raised by maker.core.ops on invalid edit requests.

    Message is written for a human: what was wrong (unknown cell,
    out-of-range tile, bad kind, bad palette name, ...), not how.
    """
