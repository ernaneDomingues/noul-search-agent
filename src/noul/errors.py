"""Exceptions shared by every Noul module."""


class HTTPError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
