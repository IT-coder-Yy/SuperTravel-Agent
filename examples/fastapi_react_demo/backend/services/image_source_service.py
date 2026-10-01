"""核对来源网页中某张图片的说明；搜索结果标题不能替代图片证据。"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


class _ImageEvidenceParser(HTMLParser):
    def __init__(self, page_url: str, image_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.image_url = image_url
        self.descriptions: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != 'img':
            return
        attrs = dict(attrs)
        urls = [attrs.get(key, '') for key in ('src', 'data-src', 'data-original')]
        urls.extend(part.strip().split(' ')[0] for part in attrs.get('srcset', '').split(','))
        if not any(urljoin(self.page_url, url) == self.image_url for url in urls if url):
            return
        # title 属性通常仍是页面标题；仅接收当前 img 元素的 alt。
        description = (attrs.get('alt') or '').strip()
        if description:
            self.descriptions.append(description)


def image_descriptions_from_html(html: str, page_url: str, image_url: str) -> list[str]:
    parser = _ImageEvidenceParser(page_url, image_url)
    parser.feed(html)
    return list(dict.fromkeys(parser.descriptions))


def fetch_image_source_descriptions(source_url: str, image_url: str) -> list[str]:
    """缺来源、失败、无对应图片或无说明都诚实返回空，不猜测画面。"""
    # 与 PDF 图片下载采用相同的公网检查（包含受信任 DoH 的代理 DNS 支持）。
    from services.pdf_export_service import _is_public_host

    current_url = source_url
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            for attempt in range(4):
                parsed = urlsplit(current_url)
                if (parsed.scheme != 'https' or not parsed.hostname
                        or parsed.username or parsed.password or parsed.port not in (None, 443)
                        or not parsed.path.strip('/') or not _is_public_host(parsed.hostname)):
                    return []
                with client.stream('GET', current_url) as response:
                    if response.is_redirect:
                        current_url = urljoin(current_url, response.headers.get('location', ''))
                        continue
                    response.raise_for_status()
                    if 'text/html' not in response.headers.get('content-type', '').lower():
                        return []
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            return []
                    html = body.decode(response.encoding or 'utf-8', errors='replace')
                    return image_descriptions_from_html(html, current_url, image_url)
    except (httpx.HTTPError, ValueError, UnicodeError):
        return []
    return []
