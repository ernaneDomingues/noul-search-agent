"""Exceptions shared by every Noul module."""


class HTTPError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# What a lookup of an external source can raise. The HTTP layer (`noul.net.http`) turns every transport failure
# into HTTPError; a payload of an unexpected shape then fails while it is read: ValueError for bad JSON, KeyError,
# IndexError, TypeError or AttributeError for missing or mistyped fields. Code that falls back when a source fails
# catches these rather than Exception, so that an error of another kind (a bug, a full disk) still surfaces.
UPSTREAM_ERRORS = (HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError)
