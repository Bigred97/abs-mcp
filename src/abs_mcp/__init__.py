from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("abs-mcp")
except PackageNotFoundError:  # editable install before metadata is generated
    __version__ = "0.0.0+unknown"
