from __future__ import annotations

from urllib.parse import urlparse

from core.ingestion.parsers.base import ParsedContent


class LinkParser:
    def parse(self, url: str) -> ParsedContent:
        parsed = urlparse(url)
        title = parsed.netloc or "Link"
        return ParsedContent(
            item_type="link",
            title=title,
            raw_content=url,
            normalized_text=url,
            metadata={"url": url, "domain": parsed.netloc},
        )
