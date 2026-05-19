from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
import logging
import os
from typing import Any
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
import trafilatura


DEFAULT_FETCH_CHARS = 4000
MAX_FETCH_CHARS = 8000
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 8
MAX_QUERY_CHARS = 400
DEFAULT_TIMEOUT_SECONDS = 20.0
USER_AGENT = "Cora/0.1 (+https://example.invalid)"
SEARCH_HTML_URL = "https://html.duckduckgo.com/html/"
SEARCH_API_URL = "https://api.duckduckgo.com/"
TAVILY_BASE_URL = "https://api.tavily.com"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str = ""


@dataclass(slots=True)
class WebSearchResult:
    query: str
    hits: list[WebSearchHit]
    provider: str = "duckduckgo"

    def render(self) -> str:
        if not self.hits:
            return f"No web search results found for `{self.query}`."
        lines = [f"Web search results for `{self.query}`:"]
        for hit in self.hits:
            lines.append(f"- {hit.title}")
            lines.append(f"  URL: {hit.url}")
            if hit.snippet:
                lines.append(f"  Snippet: {hit.snippet}")
        return "\n".join(lines)

    def metadata(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "results": [
                {
                    "title": hit.title,
                    "url": hit.url,
                    "snippet": hit.snippet,
                }
                for hit in self.hits
            ],
        }


@dataclass(slots=True)
class WebFetchResult:
    requested_url: str
    final_url: str
    status_code: int
    title: str
    content: str
    content_type: str
    truncated: bool
    provider: str = "direct"

    def render(self) -> str:
        lines = [
            f"Fetched `{self.requested_url}`.",
            f"Final URL: {self.final_url}",
            f"HTTP status: {self.status_code}",
        ]
        if self.provider != "direct":
            lines.append(f"Provider: {self.provider}")
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.content_type:
            lines.append(f"Content-Type: {self.content_type}")
        lines.extend(["", self.content or "No readable text content extracted."])
        if self.truncated:
            lines.append("")
            lines.append("[content truncated]")
        return "\n".join(lines)

    def metadata(self) -> dict[str, Any]:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "status_code": self.status_code,
            "title": self.title,
            "content_type": self.content_type,
            "truncated": self.truncated,
            "provider": self.provider,
        }


@dataclass(slots=True)
class WebToolStore:
    http_client: httpx.Client | None = None
    tavily_api_key: str | None = None
    tavily_base_url: str = TAVILY_BASE_URL

    def search(self, *, query: str, max_results: int = DEFAULT_SEARCH_LIMIT) -> WebSearchResult:
        cleaned_query = " ".join(str(query or "").split())
        if not cleaned_query:
            raise ValueError("query cannot be empty")
        if len(cleaned_query) > MAX_QUERY_CHARS:
            raise ValueError(f"query is too long; limit is {MAX_QUERY_CHARS} characters")
        result_limit = max(1, min(int(max_results or DEFAULT_SEARCH_LIMIT), MAX_SEARCH_LIMIT))
        logger.info("web search start query=%s limit=%s", cleaned_query, result_limit)
        tavily_api_key = self._resolved_tavily_api_key()
        if tavily_api_key:
            try:
                hits = self._search_tavily(
                    query=cleaned_query,
                    max_results=result_limit,
                    api_key=tavily_api_key,
                )
                logger.info(
                    "web search done query=%s provider=tavily results=%s",
                    cleaned_query,
                    len(hits),
                )
                return WebSearchResult(query=cleaned_query, hits=hits, provider="tavily")
            except Exception as exc:
                logger.warning("web search tavily failed query=%s error=%s", cleaned_query, exc)

        try:
            hits = self._search_json_api(query=cleaned_query, max_results=result_limit)
        except Exception as exc:
            logger.warning("web search json api failed query=%s error=%s", cleaned_query, exc)
            hits = []
        if len(hits) < result_limit:
            seen_urls = {hit.url for hit in hits}
            try:
                for hit in self._search_html(query=cleaned_query, max_results=result_limit):
                    if hit.url in seen_urls:
                        continue
                    hits.append(hit)
                    seen_urls.add(hit.url)
                    if len(hits) >= result_limit:
                        break
            except Exception as exc:
                logger.warning("web search html fallback failed query=%s error=%s", cleaned_query, exc)
        final_hits = hits[:result_limit]
        logger.info("web search done query=%s provider=duckduckgo results=%s", cleaned_query, len(final_hits))
        return WebSearchResult(query=cleaned_query, hits=final_hits, provider="duckduckgo")

    def fetch(self, *, url: str, max_chars: int = DEFAULT_FETCH_CHARS) -> WebFetchResult:
        normalized_url = self._normalize_url(url)
        char_limit = max(200, min(int(max_chars or DEFAULT_FETCH_CHARS), MAX_FETCH_CHARS))
        logger.info("web fetch start url=%s max_chars=%s", normalized_url, char_limit)
        direct_error: Exception | None = None
        try:
            result = self._fetch_direct(normalized_url=normalized_url, char_limit=char_limit)
            if result.content:
                return result
            logger.warning(
                "web fetch direct returned empty content url=%s final_url=%s status=%s",
                normalized_url,
                result.final_url,
                result.status_code,
            )
        except Exception as exc:
            direct_error = exc
            logger.warning(
                "web fetch direct failed url=%s error_type=%s error=%s",
                normalized_url,
                type(exc).__name__,
                exc,
            )
        tavily_api_key = self._resolved_tavily_api_key()
        if tavily_api_key:
            try:
                result = self._fetch_via_tavily_extract(
                    normalized_url=normalized_url,
                    char_limit=char_limit,
                    api_key=tavily_api_key,
                )
                logger.info(
                    "web fetch recovered via tavily_extract url=%s content_chars=%s",
                    normalized_url,
                    len(result.content),
                )
                return result
            except Exception as exc:
                logger.warning(
                    "web fetch tavily_extract failed url=%s error_type=%s error=%s",
                    normalized_url,
                    type(exc).__name__,
                    exc,
                )
                if direct_error is not None:
                    raise RuntimeError(
                        f"direct fetch failed: {type(direct_error).__name__}: {direct_error}; "
                        f"tavily extract failed: {type(exc).__name__}: {exc}"
                    ) from exc
                raise
        if direct_error is not None:
            raise direct_error
        return self._fetch_direct(normalized_url=normalized_url, char_limit=char_limit)

    def _fetch_direct(self, *, normalized_url: str, char_limit: int) -> WebFetchResult:
        response = self._client.get(normalized_url)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").strip()
        body_text = self._extract_body_text(
            raw_text=response.text,
            url=str(response.url),
            content_type=content_type,
        )
        clipped, truncated = self._clip_text(body_text, limit=char_limit)
        result = WebFetchResult(
            requested_url=normalized_url,
            final_url=str(response.url),
            status_code=response.status_code,
            title=self._extract_title(response.text),
            content=clipped,
            content_type=content_type,
            truncated=truncated,
            provider="direct",
        )
        logger.info(
            "web fetch done url=%s final_url=%s status=%s provider=%s content_chars=%s truncated=%s title=%s",
            normalized_url,
            result.final_url,
            result.status_code,
            result.provider,
            len(result.content),
            result.truncated,
            bool(result.title),
        )
        return result

    def _fetch_via_tavily_extract(self, *, normalized_url: str, char_limit: int, api_key: str) -> WebFetchResult:
        response = self._client.post(
            f"{self._normalized_tavily_base_url()}/extract",
            json={
                "urls": [normalized_url],
                "extract_depth": "advanced",
                "include_images": False,
                "include_favicon": False,
                "format": "text",
                "timeout": min(float(DEFAULT_TIMEOUT_SECONDS), 60.0),
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Tavily extract returned a non-object response")
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            failed_results = payload.get("failed_results")
            if isinstance(failed_results, list) and failed_results:
                first_error = failed_results[0]
                if isinstance(first_error, dict):
                    detail = str(first_error.get("error") or first_error.get("message") or "unknown extraction failure")
                    raise ValueError(detail)
            raise ValueError("Tavily extract returned no results")
        first_result = results[0]
        if not isinstance(first_result, dict):
            raise ValueError("Tavily extract returned an invalid result payload")
        final_url = str(first_result.get("url") or normalized_url).strip() or normalized_url
        raw_content = str(first_result.get("raw_content") or "").strip()
        if not raw_content:
            raise ValueError("Tavily extract returned empty content")
        clipped, truncated = self._clip_text(raw_content, limit=char_limit)
        result = WebFetchResult(
            requested_url=normalized_url,
            final_url=final_url,
            status_code=200,
            title="",
            content=clipped,
            content_type="text/plain; provider=tavily_extract",
            truncated=truncated,
            provider="tavily_extract",
        )
        logger.info(
            "web fetch done url=%s final_url=%s status=%s provider=%s content_chars=%s truncated=%s title=%s",
            normalized_url,
            result.final_url,
            result.status_code,
            result.provider,
            len(result.content),
            result.truncated,
            bool(result.title),
        )
        return result

    @property
    def _client(self) -> httpx.Client:
        if self.http_client is None:
            self.http_client = httpx.Client(
                follow_redirects=True,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                headers={"User-Agent": USER_AGENT},
            )
        return self.http_client

    def _search_json_api(self, *, query: str, max_results: int) -> list[WebSearchHit]:
        response = self._client.get(
            SEARCH_API_URL,
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "no_redirect": "1",
                "skip_disambig": "1",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        hits: list[WebSearchHit] = []
        for key in ("Results", "RelatedTopics"):
            self._collect_json_hits(payload.get(key), hits=hits, limit=max_results)
            if len(hits) >= max_results:
                break
        return hits[:max_results]

    def _search_html(self, *, query: str, max_results: int) -> list[WebSearchHit]:
        response = self._client.get(SEARCH_HTML_URL, params={"q": query})
        response.raise_for_status()
        parser = DuckDuckGoResultsParser()
        parser.feed(response.text)
        return parser.results[:max_results]

    def _search_tavily(self, *, query: str, max_results: int, api_key: str) -> list[WebSearchHit]:
        response = self._client.post(
            f"{self._normalized_tavily_base_url()}/search",
            json={
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        hits: list[WebSearchHit] = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip()
            title = str(entry.get("title") or "").strip()
            snippet = self._normalize_whitespace(str(entry.get("content") or entry.get("snippet") or ""))
            if not url or not title:
                continue
            hits.append(WebSearchHit(title=title, url=url, snippet=snippet))
            if len(hits) >= max_results:
                break
        return hits

    @staticmethod
    def _collect_json_hits(payload: Any, *, hits: list[WebSearchHit], limit: int) -> None:
        if len(hits) >= limit:
            return
        if isinstance(payload, dict):
            url = str(payload.get("FirstURL") or "").strip()
            title = str(payload.get("Text") or "").strip()
            if url and title:
                hits.append(WebSearchHit(title=title, url=url))
            return
        if not isinstance(payload, list):
            return
        for entry in payload:
            if len(hits) >= limit:
                return
            if isinstance(entry, dict) and "Topics" in entry:
                WebToolStore._collect_json_hits(entry.get("Topics"), hits=hits, limit=limit)
                continue
            WebToolStore._collect_json_hits(entry, hits=hits, limit=limit)

    def _resolved_tavily_api_key(self) -> str:
        explicit = str(self.tavily_api_key or "").strip()
        if explicit:
            return explicit
        return str(os.getenv("TAVILY_API_KEY") or "").strip()

    def _normalized_tavily_base_url(self) -> str:
        base_url = str(self.tavily_base_url or TAVILY_BASE_URL).strip().rstrip("/")
        if not base_url:
            return TAVILY_BASE_URL
        return base_url

    @staticmethod
    def _normalize_url(url: str) -> str:
        cleaned = str(url or "").strip()
        if not cleaned:
            raise ValueError("url cannot be empty")
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
            cleaned = f"https://{cleaned}"
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url must use http or https")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        return cleaned

    @staticmethod
    def _extract_body_text(*, raw_text: str, url: str, content_type: str) -> str:
        lowered_content_type = content_type.lower()
        if "application/json" in lowered_content_type:
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                return WebToolStore._normalize_whitespace(raw_text)
            return WebToolStore._normalize_whitespace(json.dumps(parsed, ensure_ascii=False, indent=2))
        if "text/html" not in lowered_content_type and "<html" not in raw_text.lower():
            return WebToolStore._normalize_whitespace(raw_text)
        extracted = trafilatura.extract(
            raw_text,
            url=url,
            include_links=False,
            include_images=False,
            favor_precision=True,
            output_format="txt",
        )
        if extracted:
            return WebToolStore._normalize_whitespace(extracted)
        return WebToolStore._normalize_whitespace(re.sub(r"<[^>]+>", " ", raw_text))

    @staticmethod
    def _extract_title(raw_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", raw_text, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            return ""
        return WebToolStore._normalize_whitespace(unescape(re.sub(r"<[^>]+>", " ", match.group(1))))

    @staticmethod
    def _clip_text(value: str, *, limit: int) -> tuple[str, bool]:
        compact = WebToolStore._normalize_whitespace(value)
        if len(compact) <= limit:
            return compact, False
        return compact[: limit - 3].rstrip() + "...", True

    @staticmethod
    def _normalize_whitespace(value: str) -> str:
        return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


class DuckDuckGoResultsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[WebSearchHit] = []
        self._capture_title = False
        self._current_href = ""
        self._title_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_map = {key: value or "" for key, value in attrs}
        class_name = attrs_map.get("class", "")
        href = attrs_map.get("href", "")
        if "result__a" not in class_name or not href:
            return
        self._capture_title = True
        self._current_href = self._decode_result_href(href)
        self._title_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._capture_title:
            return
        title = WebToolStore._normalize_whitespace(unescape("".join(self._title_chunks)))
        if title and self._current_href:
            self.results.append(WebSearchHit(title=title, url=self._current_href))
        self._capture_title = False
        self._current_href = ""
        self._title_chunks = []

    @staticmethod
    def _decode_result_href(href: str) -> str:
        if href.startswith("//"):
            href = f"https:{href}"
        parsed = urlparse(href)
        if "duckduckgo.com" not in parsed.netloc:
            return href
        uddg = parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return unquote(uddg[0])
        if parsed.path == "/":
            q = parse_qs(parsed.query).get("q", [])
            if q:
                return f"https://duckduckgo.com/?q={quote_plus(q[0])}"
        return href
