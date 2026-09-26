"""Bounded web search and public page fetching for the Discord bot.

Fetched page text is untrusted input. Callers must label it as such when
including it in a model prompt.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .providers import ImageInput

logger = logging.getLogger(__name__)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_ALLOWED_PAGE_TYPES = {"text/html", "text/plain"}
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_REDIRECTS = {301, 302, 303, 307, 308}


class WebError(RuntimeError):
    """Safe, user-facing web failure that never exposes upstream details."""


@dataclass(frozen=True)
class WebSource:
    title: str
    url: str
    content: str


class _TextExtractor(HTMLParser):
    """Extract readable text while excluding executable and hidden markup."""

    _SKIP = {"script", "style", "noscript", "svg", "canvas", "template", "iframe", "object"}
    _HIDDEN = {"head", "form", "button", "select", "option", "textarea"}
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    _SEPARATORS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.parts: list[str] = []
        self.length = 0
        self.stack: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        aria_hidden = (attrs_map.get("aria-hidden") or "").lower()
        style = (attrs_map.get("style") or "").replace(" ", "").lower()
        hidden = (
            tag in self._HIDDEN
            or "hidden" in attrs_map
            or aria_hidden == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        parent_mode = self.stack[-1][1] if self.stack else "text"
        if parent_mode == "skip" or tag in self._SKIP:
            mode = "skip"
        elif parent_mode == "hidden" or hidden:
            mode = "hidden"
        else:
            mode = "text"
        if mode == "text" and tag in self._SEPARATORS:
            self._append("\n")
        if tag not in self._VOID:
            self.stack.append((tag, mode))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._VOID:
            return
        previous_mode = self.stack[-1][1] if self.stack else "text"
        matching_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index][0] == tag),
            None,
        )
        if matching_index is not None:
            del self.stack[matching_index:]
        if previous_mode == "text" and tag in self._SEPARATORS:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if not self.stack or self.stack[-1][1] == "text":
            self._append(data)

    def _append(self, value: str) -> None:
        if self.length >= self.limit:
            return
        piece = value[: self.limit - self.length]
        self.parts.append(piece)
        self.length += len(piece)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line).strip()[: self.limit]


class _PublicResolver(aiohttp.abc.AbstractResolver):
    """Resolve and pin only public DNS answers to block DNS rebinding SSRF."""

    def __init__(self) -> None:
        self._inner = aiohttp.resolver.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[aiohttp.abc.ResolveResult]:
        try:
            records = await self._inner.resolve(host, port, family)
            if not records:
                raise WebError("That public address could not be reached.")
            checked: list[aiohttp.abc.ResolveResult] = []
            for record in records:
                address = ipaddress.ip_address(str(record["host"]))
                if not address.is_global:
                    raise WebError("Private or reserved network addresses are not allowed.")
                checked.append(record)
            return checked
        except WebError:
            raise
        except Exception as exc:
            logger.info("Public DNS lookup failed (%s)", type(exc).__name__)
            raise WebError("That public address could not be reached.") from None

    async def close(self) -> None:
        await self._inner.close()


class WebService:
    """Small, bounded web interface; credentials are always passed explicitly."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 10,
        max_bytes: int = 1_000_000,
        max_chars: int = 12_000,
        max_image_bytes: int = 5_000_000,
        _session_factory: Callable[..., Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_bytes < 1 or max_chars < 1 or max_image_bytes < 1:
            raise ValueError("Web limits must be positive.")
        self.api_key = api_key.strip() if api_key else None
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.max_image_bytes = max_image_bytes
        self._session_factory = _session_factory or aiohttp.ClientSession

    async def search(self, query: str) -> list[WebSource]:
        """Search Tavily and return up to five source snippets with provenance."""
        clean_query = " ".join(query.split())[:500]
        if not clean_query:
            raise WebError("Enter a search query first.")
        if not self.api_key:
            raise WebError("Web search is unavailable because no search API key is configured.")
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with self._session_factory(timeout=timeout, trust_env=False) as session:
                async with session.post(
                    _TAVILY_ENDPOINT,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "query": clean_query,
                        "search_depth": "basic",
                        "max_results": 5,
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise WebError("Web search is temporarily unavailable.")
                    raw = await _read_limited(response, self.max_bytes)
            payload = json.loads(raw)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            sources: list[WebSource] = []
            remaining_chars = self.max_chars
            for result in results:
                if not isinstance(result, dict):
                    continue
                try:
                    url = _validate_public_url(str(result.get("url", "")))
                except WebError:
                    continue
                title = _plain_text(str(result.get("title", "")), 300) or url
                metadata_chars = len(title) + len(url)
                if metadata_chars >= remaining_chars:
                    continue
                content = _plain_text(
                    str(result.get("content", "")),
                    min(self.max_chars // 5, remaining_chars - metadata_chars),
                )
                sources.append(WebSource(title=title, url=url, content=content))
                remaining_chars -= metadata_chars + len(content)
                if len(sources) == 5:
                    break
            return sources
        except WebError:
            raise
        except (
            asyncio.TimeoutError,
            aiohttp.ClientError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            logger.info("Web search failed (%s)", type(exc).__name__)
            raise WebError("Web search is temporarily unavailable.") from None

    async def browse(self, url: str) -> WebSource:
        """Fetch a small public text page; redirects and DNS are checked each hop."""
        final_url, content_type, body = await self._fetch(url, _ALLOWED_PAGE_TYPES, self.max_bytes)
        text = _extract_page_text(body, content_type, self.max_chars)
        if not text:
            raise WebError("That page did not contain readable text.")
        return WebSource(title=final_url, url=final_url, content=text)

    async def image(self, url: str) -> ImageInput:
        """Fetch a public raster image suitable for attaching to a Discord reply."""
        _final_url, content_type, body = await self._fetch(
            url, _ALLOWED_IMAGE_TYPES, self.max_image_bytes
        )
        from .media import validate_image_bytes

        try:
            mime_type = validate_image_bytes(
                body, claimed_mime=content_type, max_bytes=self.max_image_bytes
            )
        except ValueError as exc:
            raise WebError("That URL did not return a valid supported image.") from exc
        return ImageInput(data=body, mime_type=mime_type)

    async def _fetch(
        self, url: str, allowed_types: set[str], byte_limit: int
    ) -> tuple[str, str, bytes]:
        current = _validate_public_url(url)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        connector = aiohttp.TCPConnector(resolver=_PublicResolver(), use_dns_cache=False, limit=4)
        try:
            async with self._session_factory(
                timeout=timeout,
                connector=connector,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                for hop in range(4):
                    async with session.get(
                        current,
                        headers={
                            "Accept": ", ".join(sorted(allowed_types)),
                            "Accept-Encoding": "identity",
                        },
                        allow_redirects=False,
                    ) as response:
                        if response.status in _REDIRECTS:
                            if hop == 3:
                                raise WebError("The page redirected too many times.")
                            location = response.headers.get("Location")
                            if not location:
                                raise WebError("That page returned an invalid redirect.")
                            from urllib.parse import urljoin

                            current = _validate_public_url(urljoin(current, location))
                            continue
                        if response.status < 200 or response.status >= 300:
                            raise WebError("That public page could not be fetched.")
                        content_type = (
                            response.headers.get("Content-Type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if content_type not in allowed_types:
                            raise WebError(
                                "That URL did not return a supported text page or image."
                            )
                        body = await _read_limited(response, byte_limit)
                        return current, content_type, body
        except WebError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
            logger.info("Public page fetch failed (%s)", type(exc).__name__)
            raise WebError("That public page could not be fetched.") from None
        raise WebError("That public page could not be fetched.")


def _validate_public_url(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        port = parsed.port
        if port is not None and port not in {80, 443}:
            raise ValueError
        host = parsed.hostname.rstrip(".")
        if not host or any(ord(char) < 33 for char in host):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Reject non-public numeric forms that browsers may interpret as IPs.
            if all(char in "0123456789." for char in host):
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
        netloc = host
        if ":" in host:
            netloc = f"[{host}]"
        if port is not None:
            netloc = f"{netloc}:{port}"
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError):
        raise WebError("Only public HTTP or HTTPS URLs are allowed.") from None


async def _read_limited(response: Any, limit: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > limit:
                raise WebError("The response was too large to process.")
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(min(65_536, limit + 1)):
        size += len(chunk)
        if size > limit:
            raise WebError("The response was too large to process.")
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_page_text(body: bytes, content_type: str, limit: int) -> str:
    text = body.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        return _plain_text(text, limit)
    parser = _TextExtractor(limit)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return ""
    return parser.text()


def _plain_text(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit].strip()
