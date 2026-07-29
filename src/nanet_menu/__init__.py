"""National Assembly Library daily menu bot."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nanet-menu")
except PackageNotFoundError:
    __version__ = "0.0.0"
