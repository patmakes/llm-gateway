"""
Minimal remote MCP server exposing two tools to llama-server (or any MCP client):

  web_search(query, ...)  -> results from a self-hosted SearXNG instance
  web_fetch(url, ...)     -> readable text of a public web page

Transport: MCP "Streamable HTTP" at  POST/GET/DELETE  /mcp
  - Client POSTs JSON-RPC messages (initialize, tools/list, tools/call ...)
  - Server answers with JSON or an SSE stream on the same response
  - Session tracked with the Mcp-Session-Id header

nginx on the gateway terminates the client's TLS, checks the bearer token,
and proxies https://<gateway>/mcp -> https://<mcp-vm>:8000/mcp.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# --------------------------------------------------------------------------- config
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
FETCH_MAX_BYTES = int(os.environ.get("FETCH_MAX_BYTES", str(5 * 1024 * 1024)))
FETCH_TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "20"))
FETCH_MAX_REDIRECTS = 5
USER_AGENT = os.environ.get("FETCH_USER_AGENT", "Mozilla/5.0 (compatible; homelab-mcp-fetch/1.0)")

mcp = FastMCP(
    name="web-tools",
    instructions="Use web_search to find pages, then web_fetch to read a specific URL.",
    # Stateless: every request is independent, so restarts/rebuilds of the VM
    # never strand a client holding a dead Mcp-Session-Id.
    stateless_http=True,
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        # Host-header check (DNS-rebinding protection). Must include the Host
        # nginx sends upstream (the gateway sets it to the MCP VM name).
        enable_dns_rebinding_protection=bool(ALLOWED_HOSTS),
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=[f"https://{h.split(':')[0]}" for h in ALLOWED_HOSTS],
    ),
)


# --------------------------------------------------------------------------- tools
@mcp.tool()
async def web_search(query: str, max_results: int = 8, categories: str = "general",
                     time_range: str | None = None) -> str:
    """Search the web. Returns a numbered list of results (title, URL, snippet).

    Args:
        query: search terms.
        max_results: 1-20 results to return.
        categories: SearXNG category, e.g. general, news, it, science.
        time_range: optional recency filter: day, week, month or year.
    """
    params = {"q": query, "format": "json", "categories": categories}
    if time_range in {"day", "week", "month", "year"}:
        params["time_range"] = time_range
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{SEARXNG_URL}/search", params=params)
        r.raise_for_status()
        results = r.json().get("results", [])[: max(1, min(max_results, 20))]
    if not results:
        return f"No results for: {query}"
    lines = []
    for i, res in enumerate(results, 1):
        snippet = (res.get("content") or "").strip().replace("\n", " ")
        lines.append(f"{i}. {res.get('title', '(no title)')}\n   {res.get('url')}\n   {snippet[:300]}")
    return "\n".join(lines)


@mcp.tool()
async def web_fetch(url: str, max_chars: int = 20000, start_char: int = 0) -> str:
    """Fetch a public web page and return its main text content (boilerplate removed).

    Args:
        url: absolute http(s) URL.
        max_chars: maximum characters to return (paginate with start_char).
        start_char: offset into the extracted text, for reading long pages in chunks.
    """
    body, final_url, ctype = await _safe_get(url)
    if "html" in ctype or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        text = trafilatura.extract(body.decode("utf-8", "replace"), url=final_url,
                                   include_links=False, include_tables=True) or ""
    elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
        text = body.decode("utf-8", "replace")
    else:
        return f"Unsupported content type '{ctype}' at {final_url}"
    total = len(text)
    chunk = text[start_char : start_char + max(500, min(max_chars, 100_000))]
    more = start_char + len(chunk) < total
    footer = f"\n\n[chars {start_char}-{start_char + len(chunk)} of {total}" + \
             (f"; call again with start_char={start_char + len(chunk)} for more]" if more else "]")
    return f"URL: {final_url}\n\n{chunk}{footer}"


# --------------------------------------------------------------------------- SSRF guard
def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    # 100.64.0.0/10 (CGNAT / Tailscale) is not flagged by is_private on all Pythons.
    if addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return addr.is_global and not (addr.is_multicast or addr.is_reserved)


async def _check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Only absolute http(s) URLs are allowed")
    if p.port not in (None, 80, 443, 8080, 8443):
        raise ValueError(f"Port {p.port} is not allowed")
    infos = await asyncio.get_running_loop().getaddrinfo(p.hostname, p.port or 443,
                                                         type=socket.SOCK_STREAM)
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise ValueError(f"Refusing to fetch non-public address {info[4][0]} ({p.hostname})")


async def _safe_get(url: str) -> tuple[bytes, str, str]:
    """GET with per-hop address checks, manual redirects and a size cap.

    This is a best-effort application check; the VM egress firewall is the real
    control (it also covers DNS-rebinding races between the check and connect).
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False,
                                 headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(FETCH_MAX_REDIRECTS + 1):
            await _check_url(url)
            async with client.stream("GET", url) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    continue
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > FETCH_MAX_BYTES:
                        break
                return bytes(buf[:FETCH_MAX_BYTES]), str(r.url), r.headers.get("content-type", "").lower()
        raise ValueError("Too many redirects")


# --------------------------------------------------------------------------- entrypoint
if __name__ == "__main__":
    tls = {}
    if os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"):
        tls = {"ssl_certfile": os.environ["TLS_CERT"], "ssl_keyfile": os.environ["TLS_KEY"]}
    uvicorn.run(
        mcp.streamable_http_app(),            # ASGI app serving /mcp
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        **tls,
    )
