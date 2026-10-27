"""Tiny fake for `requests`: routes by (method, url regex) and records every call."""
import re, json as _json
import requests


class FakeResponse:
    def __init__(self, status=200, json=None, content=b"", text=None, lines=None, url=""):
        self.status_code = status
        self._json = json
        self.content = content if json is None else _json.dumps(json).encode()
        self.text = text if text is not None else (self.content.decode("utf-8", "replace") if self.content else "")
        self._lines = lines
        self.url = url
        self.headers = {}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        if self._json is None:
            return _json.loads(self.text)
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, size=1024):
        yield self.content

    def iter_lines(self, decode_unicode=False):
        for line in self._lines or []:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Router:
    def __init__(self):
        self.routes, self.calls = [], []

    def add(self, method, pattern, handler):
        """handler: FakeResponse or callable(call_dict) -> FakeResponse"""
        self.routes.insert(0, (method.upper(), re.compile(pattern), handler))

    def clear(self):
        self.routes, self.calls = [], []

    def _do(self, method, url, **kw):
        call = dict(method=method.upper(), url=url, **kw)
        self.calls.append(call)
        for m, rx, handler in self.routes:
            if m == method.upper() and rx.search(url):
                resp = handler(call) if callable(handler) else handler
                resp.url = url
                return resp
        raise AssertionError(f"unexpected HTTP {method} {url}")

    def install(self):
        requests.get = lambda url, **kw: self._do("GET", url, **kw)
        requests.post = lambda url, **kw: self._do("POST", url, **kw)
        requests.request = lambda method, url, **kw: self._do(method, url, **kw)

    def last(self, pattern, method=None):
        for c in reversed(self.calls):
            if re.search(pattern, c["url"]) and (method is None or c["method"] == method):
                return c
        return None
