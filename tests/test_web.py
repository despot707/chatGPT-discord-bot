from __future__ import annotations

import io
import json
import socket
from collections import deque
from typing import Any

import pytest
from PIL import Image
from src.web import WebError, WebService, WebSource, _PublicResolver, _validate_public_url


class _Content:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, _size: int):
        for offset in range(0, len(self.body), 8):
            yield self.body[offset : offset + 8]


class _Response:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status = status
        self.headers = headers or {}
        self.content = _Content(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Session:
    def __init__(self, responses: deque[_Response]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("GET", url, kwargs))
        return self.responses.popleft()

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("POST", url, kwargs))
        return self.responses.popleft()


class _Resolver:
    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers

    async def resolve(self, host: str, port: int, family: int = socket.AF_INET):
        return self.answers

    async def close(self) -> None:
        return None


def _service(responses: list[_Response], **kwargs: Any) -> tuple[WebService, list[_Session]]:
    pending = deque(responses)
    sessions: list[_Session] = []

    def factory(**_kwargs: Any) -> _Session:
        session = _Session(pending)
        sessions.append(session)
        return session

    return WebService(_session_factory=factory, **kwargs), sessions


def test_url_validation_rejects_credentials_private_and_nonstandard_ports() -> None:
    for url in (
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:127.0.0.1]/",
        "http://example.com:8080/",
        "http://2130706433/",
    ):
        with pytest.raises(WebError):
            _validate_public_url(url)


@pytest.mark.asyncio
async def test_resolver_rejects_mixed_public_and_private_dns_answers() -> None:
    resolver = _PublicResolver()
    resolver._inner = _Resolver(
        [
            {"host": "93.184.216.34", "port": 443, "family": socket.AF_INET},
            {"host": "10.0.0.5", "port": 443, "family": socket.AF_INET},
        ]
    )
    with pytest.raises(WebError, match="Private or reserved"):
        await resolver.resolve("example.com", 443, socket.AF_UNSPEC)


@pytest.mark.asyncio
async def test_search_uses_bearer_auth_and_returns_validated_provenance() -> None:
    body = json.dumps(
        {
            "results": [
                {
                    "title": "Useful page",
                    "url": "https://example.com/a#part",
                    "content": "A   snippet",
                },
                {"title": "internal", "url": "http://127.0.0.1/", "content": "skip"},
            ]
        }
    ).encode()
    service, sessions = _service(
        [_Response(200, body, {"Content-Length": str(len(body))})], api_key="secret-test-token"
    )
    results = await service.search("  Find  useful information ")
    assert results == [WebSource("Useful page", "https://example.com/a", "A snippet")]
    method, url, options = sessions[0].calls[0]
    assert (method, url) == ("POST", "https://api.tavily.com/search")
    assert options["headers"]["Authorization"] == "Bearer secret-test-token"
    assert options["json"]["query"] == "Find useful information"
    assert options["json"]["max_results"] == 5
    assert options["allow_redirects"] is False


@pytest.mark.asyncio
async def test_search_requires_key_and_hides_upstream_errors() -> None:
    with pytest.raises(WebError, match="no search API key"):
        await WebService().search("hello")
    service, _ = _service([_Response(403, b"contains-secret-provider-detail")], api_key="token")
    with pytest.raises(WebError, match="temporarily unavailable") as failure:
        await service.search("hello")
    assert "contains-secret" not in str(failure.value)


@pytest.mark.asyncio
async def test_search_bounds_combined_snippets_and_metadata() -> None:
    body = json.dumps(
        {
            "results": [
                {
                    "title": "Source " + str(index),
                    "url": f"https://example.com/{index}",
                    "content": "x" * 12000,
                }
                for index in range(5)
            ]
        }
    ).encode()
    service, _ = _service([_Response(200, body)], api_key="placeholder", max_chars=12000)
    sources = await service.search("question")
    assert len(sources) == 5
    assert (
        sum(len(source.title) + len(source.url) + len(source.content) for source in sources)
        <= 12000
    )


@pytest.mark.asyncio
async def test_browse_strips_scripts_forms_and_hidden_text() -> None:
    page = b"""<!doctype html><html><head>
    <meta charset="utf-8"><link rel="stylesheet" href="/site.css">
    <title>ignored head</title></head><body>
    <h1>Public heading</h1><p>Useful text</p><script>secret()</script>
    <form><input type="text" value='do not read'><div>form secret</div>
    <button>button secret</button></form>
    <div hidden>hidden secret</div><div style='display: none'>also secret</div>
    <style>CSS secret</style><p>Visible after void tags</p></body></html>"""
    service, sessions = _service(
        [_Response(200, page, {"Content-Type": "text/html; charset=utf-8"})]
    )
    result = await service.browse("https://example.com/page")
    assert result.url == "https://example.com/page"
    assert "Public heading" in result.content and "Useful text" in result.content
    assert "Visible after void tags" in result.content
    for excluded in ("secret", "ignored head", "do not read", "button secret"):
        assert excluded not in result.content
    assert sessions[0].calls[0][2]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_rejected_before_following() -> None:
    service, sessions = _service([_Response(302, headers={"Location": "http://127.0.0.1/admin"})])
    with pytest.raises(WebError, match="public HTTP or HTTPS"):
        await service.browse("https://example.com/")
    assert len(sessions[0].calls) == 1


@pytest.mark.asyncio
async def test_browse_rejects_large_payload_even_without_content_length() -> None:
    service, _ = _service(
        [_Response(200, b"x" * 120, {"Content-Type": "text/plain"})], max_bytes=50
    )
    with pytest.raises(WebError, match="too large"):
        await service.browse("https://example.com/")


@pytest.mark.asyncio
async def test_browse_rejects_unsupported_mime_and_bad_redirect_chain() -> None:
    service, _ = _service([_Response(200, b"<html/>", {"Content-Type": "application/pdf"})])
    with pytest.raises(WebError, match="supported text page or image"):
        await service.browse("https://example.com/")
    redirects = [_Response(302, headers={"Location": "/again"}) for _ in range(4)]
    redirecting, _ = _service(redirects)
    with pytest.raises(WebError, match="too many times"):
        await redirecting.browse("https://example.com/")


@pytest.mark.asyncio
async def test_image_fetch_returns_verified_raster_image() -> None:
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, format="PNG")
    body = output.getvalue()
    service, _ = _service(
        [_Response(200, body, {"Content-Type": "image/png", "Content-Length": str(len(body))})]
    )
    result = await service.image("https://images.example.com/photo.png")
    assert result.data == body
    assert result.mime_type == "image/png"


@pytest.mark.asyncio
async def test_image_fetch_rejects_html_and_corrupt_raster() -> None:
    html_service, _ = _service(
        [_Response(200, b"<html>not an image</html>", {"Content-Type": "text/html"})]
    )
    with pytest.raises(WebError, match="supported text page or image"):
        await html_service.image("https://images.example.com/photo.png")
    corrupt_service, _ = _service([_Response(200, b"not png", {"Content-Type": "image/png"})])
    with pytest.raises(WebError, match="valid supported image"):
        await corrupt_service.image("https://images.example.com/photo.png")
