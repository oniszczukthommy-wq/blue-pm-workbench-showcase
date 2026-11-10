"""Bounded public evidence collection through DeepSeek Search & Fetch.

Only the two fixed provider endpoints receive the API key. Retrieved pages are
data for later analysis; this module never executes their contents or requests
their URLs from this computer. Successful responses are cached before synthesis
so a failed report can reuse its evidence without paying for retrieval again.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.engine import GenerationInterrupted, config
from backend.store import now

API_ROOT = "https://api.deepseek.com/v1/tools/"
CACHE_VERSION = 1
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CONTENT = 6000
CONTENT_BUDGET = 80000
CHANNELS = (
    ("xiaohongshu", "小红书", ("xiaohongshu.com",)),
    ("bilibili", "B站", ("bilibili.com",)),
    ("douyin", "抖音", ("douyin.com",)),
    ("wechat", "微信公众号", ("mp.weixin.qq.com",)),
    ("woshipm", "人人都是产品经理", ("woshipm.com",)),
    ("sspai", "少数派", ("sspai.com",)),
    ("overseas", "X / 海外科技媒体 / Reddit", ("x.com", "reddit.com", "techcrunch.com", "theverge.com")),
)


def public_url(value):
    """Return a normalized HTTP(S) URL, rejecting local/credentialed targets.

This is a target validation boundary, not a guarantee about a remote provider's
redirects. Fetch does not report its final URL; reports retain the requested URL.
"""
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    if "\\" in value or any(ord(c) < 33 or c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or parts.username is not None or parts.password is not None:
            return None
        host = (parts.hostname or "").rstrip(".").lower()
        if not host or "%" in host or parts.port == 0:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            host = host.encode("idna").decode("ascii")
            if len(host) > 253 or "." not in host or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")
            ):
                return None
            if host in ("localhost", "localhost.localdomain") or host.endswith((
                ".localhost", ".local", ".localdomain", ".lan", ".internal", ".intranet",
                ".home", ".home.arpa", ".corp", ".test", ".invalid", ".example", ".onion",
            )):
                return None
            # Non-canonical numeric host spellings may be interpreted as loopback
            # by URL clients even though ipaddress rejects them.
            if all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in host.split(".")):
                return None
        else:
            if (not address.is_global or address.is_multicast or address.is_reserved
                    or address.is_unspecified or getattr(address, "is_site_local", False)):
                return None
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        port = parts.port
        netloc = host + (f":{port}" if port and port != {"http": 80, "https": 443}[parts.scheme.lower()] else "")
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return None


def _in_domains(url, domains):
    host = urlsplit(url).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _safe_error(exc):
    if isinstance(exc, httpx.TimeoutException):
        return "检索服务超时，本次未自动重试"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return "检索接口未授权，请检查 DeepSeek API Key 或权限"
        if status == 402:
            return "检索服务余额不足"
        if status == 429:
            return "检索服务限流，请稍后手动重试"
        return f"检索服务返回 HTTP {status}，本次未自动重试"
    if isinstance(exc, httpx.RequestError):
        return "无法连接检索服务，请检查网络后手动重试"
    return "检索接口响应不符合预期，本次未自动重试"


class RetrievalFailure(Exception):
    """Contains only a safe user-facing error, never request headers or bodies."""


class _Provider:
    def __init__(self, root, stop_event, usage, persist):
        self.root, self.stop_event, self.usage, self.persist = root, stop_event, usage, persist
        self.key = None

    def check_stop(self):
        if self.stop_event.is_set():
            raise GenerationInterrupted()

    def request(self, operation, body):
        self.check_stop()
        request = {"operation": operation, "body": body}
        path = self.root / (_digest(request) + ".json")
        cached = None
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if candidate.get("version") == CACHE_VERSION and candidate.get("request") == request:
                self._validate(operation, candidate["response"])
                if isinstance(candidate["retrieved_at"], str):
                    cached = candidate
        except (OSError, ValueError, KeyError, AttributeError, RetrievalFailure):
            cached = None
        if cached is not None:
            result = cached["response"]
            self.usage["cache_hits"] += 1
            if (bool(result["results"]) if operation == "search" else bool(result["content"].strip())):
                self.usage["cached_nonempty_responses"] += 1
            # A snapshot write failure must not fall through to another paid
            # request when the response cache itself was already valid.
            self.persist()
            return result, cached["retrieved_at"]
        self.check_stop()
        if self.key is None:
            self.key = config()["key"]
        if not self.key:
            raise RetrievalFailure("请先配置 DeepSeek API Key，再开始联网搜证")
        self.usage["requests"] += 1
        self.usage[f"{operation}_requests"] += 1
        try:
            # Trust only the fixed TLS provider endpoint. Disable environment
            # proxies and redirects so credentials cannot follow another host.
            timeout = httpx.Timeout(connect=5, read=25, write=5, pool=5)
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", API_ROOT + operation, headers={
                    "Authorization": "Bearer " + self.key,
                    "Content-Type": "application/json", "User-Agent": "PM-Workbench/1.0",
                }, json=body) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        self.check_stop()
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ValueError("oversize provider response")
                        chunks.append(chunk)
                    result = json.loads(b"".join(chunks))
            self._validate(operation, result)
            stamp = now()
            self.usage["completed_requests"] += 1
            if (bool(result["results"]) if operation == "search" else bool(result["content"].strip())):
                self.usage["successful_nonempty_requests"] += 1
            _save(path, {"version": CACHE_VERSION, "request": request, "retrieved_at": stamp, "response": result})
            return result, stamp
        except GenerationInterrupted:
            self.usage["interrupted_requests"] += 1
            raise
        except Exception as exc:
            self.usage["failed_requests"] += 1
            raise RetrievalFailure(_safe_error(exc)) from None
        finally:
            self.persist()

    @staticmethod
    def _validate(operation, result):
        if not isinstance(result, dict):
            raise RetrievalFailure("检索接口响应不符合预期")
        if operation == "search":
            if not isinstance(result.get("results"), list) or any(not isinstance(item, dict) for item in result["results"]):
                raise RetrievalFailure("检索接口响应不符合预期")
        elif not isinstance(result.get("title"), str) or not isinstance(result.get("content"), str):
            raise RetrievalFailure("抓取接口响应不符合预期")


def _query(name, domains=(), suffix="体验 评测"):
    operators = " OR ".join("site:" + domain for domain in domains)
    tail = " " + suffix + (" " + operators if operators else "")
    # A product name is text, not a provider search program. In particular it
    # cannot inject another site: restriction to silently defeat the channels.
    name = re.sub(r"[\s\"'():|+-]+", " ", name).strip() or "产品"
    return name[: max(1, 128 - len(tail))] + tail


def _text(value, length):
    return value[:length] if isinstance(value, str) else ""


def collect_research(payload, cache_dir, stop_event, log):
    """Search seven channels, supplement the product page, and fetch ≤4 pages.

    No report or claim is generated here. Search-only hits remain explicitly
    labelled as summaries; blocked and unavailable pages do not become facts.
    Request counts are for this run, while cache hits reuse earlier responses.
    The safe research-summary.json is refreshed throughout and survives errors.
    """
    cache_dir = Path(cache_dir)
    inputs = {key: payload.get(key, "") for key in ("product_name", "product_url", "focus", "depth")}
    identity = _digest({"version": CACHE_VERSION, "input": inputs})
    root = cache_dir / "research" / identity
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    sources, by_url = [], {}
    channels = [{"id": channel, "name": name, "status": "pending", "query": _query(inputs["product_name"], domains),
                 "source_count": 0, "error": None} for channel, name, domains in CHANNELS]
    usage = {"provider": "deepseek-search-fetch", "requests": 0, "search_requests": 0, "fetch_requests": 0,
             "completed_requests": 0, "successful_nonempty_requests": 0, "failed_requests": 0,
             "interrupted_requests": 0, "cache_hits": 0, "cached_nonempty_responses": 0,
             "billing_note": "联网检索单独计费；请求数为本次尝试，缓存复用不再次请求。超时或中断的实际计费以供应商账单为准。"}
    result = {"schema_version": CACHE_VERSION, "input_sha256": identity, "product_name": inputs["product_name"],
              "product_url": public_url(inputs["product_url"]) or "", "sources": sources, "channels": channels,
              "retrieval_usage": usage, "supplementary": {"status": "pending", "source_count": 0},
              "limitations": ["仅检索公开可索引内容；登录、付费或平台封闭内容不代表已访问。",
                              "搜索结果与页面文本均为外部证据，可能不完整或过时。",
                              "抓取接口不提供跳转后的最终网址，引用保留原请求网址。"]}

    def persist():
        remaining = CONTENT_BUDGET
        for source in sources:
            source["content"] = source["content"][:min(MAX_CONTENT, remaining)]
            remaining -= len(source["content"])
        result["updated_at"] = now()
        _save(cache_dir / "research-summary.json", result)

    provider = _Provider(root, stop_event, usage, persist)
    persist()

    def add_source(item, channel, stamp):
        url = public_url(item.get("url"))
        if not url:
            return None
        if url in by_url:
            source = by_url[url]
            if channel not in source["channels"]:
                source["channels"].append(channel)
            return source
        body = _text(item.get("content"), MAX_CONTENT)
        source = {"id": "S" + hashlib.sha256(url.encode()).hexdigest()[:12], "url": url,
                  "title": _text(item.get("title"), 300) or urlsplit(url).hostname,
                  "channel": channel, "channels": [channel], "retrieved_at": stamp,
                  "snippet": _text(item.get("snippet") or item.get("summary"), 1200),
                  "content": body or _text(item.get("summary") or item.get("snippet"), MAX_CONTENT),
                  "source_type": "search_content" if body.strip() else "search_summary",
                  "fetch_status": "not_requested", "date": _text(item.get("date"), 80)}
        sources.append(source)
        by_url[url] = source
        return source

    try:
        for channel, (_, label, domains) in zip(channels, CHANNELS):
            provider.check_stop()
            log("联网搜证：" + label)
            try:
                response, stamp = provider.request("search", {"query": channel["query"], "count": 3, "need_content": True})
                hits = []
                for item in response["results"]:
                    url = public_url(item.get("url"))
                    if url and _in_domains(url, domains) and len(hits) < 3 and url not in hits:
                        hits.append(url)
                        add_source(item, channel["id"], stamp)
                channel.update(status="completed" if hits else "no_results", source_count=len(hits))
                if not hits:
                    channel["note"] = "没有搜到符合该渠道域名的公开结果"
            except RetrievalFailure as exc:
                channel.update(status="error", error=str(exc))
            finally:
                persist()

        provider.check_stop()
        log("联网搜证：补充产品页与公开文档")
        product_url = public_url(inputs["product_url"])
        domains = (urlsplit(product_url).hostname,) if product_url else ()
        supplementary = result["supplementary"]
        supplementary["query"] = _query(inputs["product_name"], domains, "官方 产品 功能 文档")
        try:
            response, stamp = provider.request("search", {"query": supplementary["query"], "count": 3, "need_content": True})
            hits = []
            for item in response["results"]:
                url = public_url(item.get("url"))
                if url and (not domains or _in_domains(url, domains)) and len(hits) < 3 and url not in hits:
                    hits.append(url)
                    add_source(item, "supplementary", stamp)
            supplementary.update(status="completed" if hits else "no_results", source_count=len(hits))
        except RetrievalFailure as exc:
            supplementary.update(status="error", error=str(exc))
        finally:
            persist()

        # Fetch the user-specified page, or the first supplementary search hit.
        # That search hit is a candidate product page, never assumed official.
        if product_url and product_url not in by_url:
            add_source({"url": product_url, "title": "用户提供的产品页"}, "supplementary", now())
        primary = product_url or next((s["url"] for s in sources if "supplementary" in s["channels"]), None)
        candidates = [primary] if primary else []
        for channel, _, _ in CHANNELS:
            candidate = next((s["url"] for s in sources if channel in s["channels"] and s["source_type"] == "search_summary"), None)
            if candidate and candidate not in candidates and len(candidates) < 4:
                candidates.append(candidate)
        for url in candidates[:4]:
            provider.check_stop()
            source = by_url[url]
            log("读取公开页面：" + str(urlsplit(url).hostname))
            try:
                page, stamp = provider.request("fetch", {"url": url})
                body = page["content"].strip()
                blocked = len(body) < 800 and bool(re.search(
                    r"(?:请先登录|登录后(?:查看|访问)|扫码登录|访问受限|验证码|access denied|sign in to (?:view|continue)|403 forbidden)", body, re.I))
                if not body or blocked:
                    source["fetch_status"] = "blocked" if blocked else "empty"
                    source["fetch_note"] = "页面需要登录或访问受限，保留搜索证据" if blocked else "未抓取到正文，保留搜索证据"
                else:
                    source.update(content=body[:MAX_CONTENT], source_type="fetched_page", fetch_status="available",
                                  fetched_at=stamp, title=page["title"][:300] or source["title"])
            except RetrievalFailure as exc:
                source.update(fetch_status="error", fetch_note=str(exc))
            finally:
                persist()
        # An unfetched input URL alone is not evidence that a page was read.
        sources[:] = [source for source in sources if source["content"].strip() or source["snippet"].strip()]
        result["completed"] = True
        log(f"公开搜证完成：{len(sources)} 条来源；七渠道覆盖 {sum(c['status'] == 'completed' for c in channels)}/7")
        return result
    finally:
        persist()
