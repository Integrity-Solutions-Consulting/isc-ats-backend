"""Tests for app.core.client_ip.get_client_ip — real client IP resolution.

The API runs behind the Next.js proxy on an internal network, so the peer
address is always the proxy. get_client_ip trusts the X-Real-Client-IP header
(set by our proxy) only when trust_proxy_headers is enabled.
"""

from starlette.requests import Request

from app.core.client_ip import get_client_ip
from app.core.config import settings


def _make_request(headers: dict[str, str], client_host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_prefers_forwarded_header_when_trusted(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    req = _make_request({"X-Real-Client-IP": "203.0.113.7"}, client_host="10.0.0.1")
    assert get_client_ip(req) == "203.0.113.7"


def test_ignores_forwarded_header_when_untrusted(monkeypatch) -> None:
    # Default posture: a client-supplied header must never override the peer.
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    req = _make_request({"X-Real-Client-IP": "203.0.113.7"}, client_host="10.0.0.1")
    assert get_client_ip(req) == "10.0.0.1"


def test_falls_back_to_peer_when_header_absent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    req = _make_request({}, client_host="10.0.0.1")
    assert get_client_ip(req) == "10.0.0.1"
