class HcawnError(Exception):
    """Base class of all hcawn-ha errors."""


class NativeUnavailable(HcawnError):
    """The compiled engine is not part of this install (it ships for Linux x86-64)."""


class WeightsNotFound(HcawnError):
    """No weights file for the requested model."""
