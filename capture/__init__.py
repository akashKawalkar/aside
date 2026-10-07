# capture/__init__.py
#
# Public surface of chunk A. server.py is the composition root; everything
# else in here is internal to the chunk.

from capture.server import create_app

__all__ = ["create_app"]