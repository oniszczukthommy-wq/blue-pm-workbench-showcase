"""Render a standalone, inert HTML product teardown using only the standard library.

Markdown and evidence are untrusted text. This renderer deliberately implements a
small Markdown subset rather than allowing HTML from the report into the page.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from urllib.parse import urlsplit


_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
_INLINE = re.compile(
    r"(?P<code>`(?P<code_text>[^`\n]+)`)|"
    r"(?P<strong>\*\*(?P<strong_text>[^\n]+?)\*\*)|"
    r"(?P<link>\[(?P<link_text>[^\]\n]+)\]\((?P<link_url>[^\s()]+)\))"
)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
_LIST = re.compile(r"^\s*(?:(?P<ordered>\d+)[.)]|[-*+])\s+(.+)$")
_CHAPTER = re.compile(r"^(?:第\s*)?([0-6０-６])(?:\s*章)?(?=[.、:：\s]|$)")
_TONES = {
    "blue": "blue", "indigo": "blue", "cyan": "teal", "teal": "teal",
    "green": "teal", "positive": "teal", "purple": "violet", "violet": "violet",
    "amber": "amber", "orange": "amber", "warning": "amber",
    "red": "rose", "rose": "rose", "risk": "rose", "neutral": "blue",
}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _escape(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _items(value: object) -> list[Mapping]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _safe_url(value: object) -> str | None:
    """Allow navigable HTTP(S) links, rejecting browser/parser ambiguities."""
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or not parts.netloc or parts.username is not None or parts.password is not None):
            return None
        # Accessing port also rejects malformed ports and invalid bracketed hosts.
        _ = parts.port
    except ValueError:
        return None
    return value


def _link(label: str, url: object, *, formatted: bool = False) -> str:
    label_html = _inline(label, links=False) if formatted else _escape(label)
    safe = _safe_url(url)
    if safe is None:
        return label_html
    return f'<a href="{_escape(safe)}" rel="noreferrer noopener">{label_html}</a>'


def _inline(value: str, *, links: bool = True, depth: int = 0) -> str:
    if depth > 4:
        return _escape(value)
    output: list[str] = []
    end = 0
    for match in _INLINE.finditer(value):
        output.append(_escape(value[end:match.start()]))
        if match.group("code"):
            output.append(f'<code>{_escape(match.group("code_text"))}</code>')
        elif match.group("strong"):
            content = _inline(match.group("strong_text"), links=links, depth=depth + 1)
            output.append(f"<strong>{content}</strong>")
        elif links:
            output.append(_link(match.group("link_text"), match.group("link_url"), formatted=True))
        else:
            output.append(_escape(match.group(0)))
        end = match.end()
    output.append(_escape(value[end:]))
    return "".join(output)


def _table_cells(line: str) -> list[str]:
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _chapter_number(title: str) -> int | None:
    match = _CHAPTER.match(title.strip().removeprefix("**"))
    if not match:
        return None
    return ord(match.group(1)) - (ord("０") if "０" <= match.group(1) <= "６" else ord("0"))


def _markdown(content: str, visuals: dict[int, str]) -> str:
    """Render headings, paragraphs, tables, lists, quotes and fenced/inline code."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    inserted: set[int] = set()
    chapters: set[int] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            chapter = _chapter_number(title)
            anchor = f' id="chapter-{chapter}"' if chapter is not None and chapter not in chapters else ""
            if chapter is not None:
                chapters.add(chapter)
            output.append(f"<h{level}{anchor}>{_inline(title)}</h{level}>")
            if chapter in visuals and chapter not in inserted:
                output.append(visuals[chapter])
                inserted.add(chapter)
            index += 1
            continue
        if line.lstrip().startswith(("```", "~~~")):
            fence = line.lstrip()[:3]
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].lstrip().startswith(fence):
                code.append(lines[index])
                index += 1
            output.append(f'<pre><code>{_escape(chr(10).join(code))}</code></pre>')
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _table_separator(lines[index + 1]):
            headers = _table_cells(line)
            output.append('<div class="table-scroll"><table class="markdown-table"><thead><tr>')
            output.extend(f'<th scope="col">{_inline(cell)}</th>' for cell in headers)
            output.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                cells = _table_cells(lines[index])
                cells = (cells + [""] * len(headers))[:len(headers)]
                output.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
                index += 1
            output.append("</tbody></table></div>")
            continue
        list_item = _LIST.match(line)
        if list_item:
            ordered = list_item.group("ordered") is not None
            tag = "ol" if ordered else "ul"
            output.append(f"<{tag}>")
            while index < len(lines):
                current = _LIST.match(lines[index])
                if not current or (current.group("ordered") is not None) != ordered:
                    break
                output.append(f"<li>{_inline(current.group(2))}</li>")
                index += 1
            output.append(f"</{tag}>")
            continue
        if line.lstrip().startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote.append(lines[index].lstrip()[1:].strip())
                index += 1
            output.append(f'<blockquote><p>{_inline(chr(10).join(quote))}</p></blockquote>')
            continue
        if re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            output.append("<hr>")
            index += 1
            continue
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            candidate = lines[index]
            if (_HEADING.match(candidate) or _LIST.match(candidate)
                    or candidate.lstrip().startswith(("```", "~~~", ">"))
                    or (index + 1 < len(lines) and "|" in candidate and _table_separator(lines[index + 1]))):
                break
            paragraph.append(candidate)
            index += 1
        output.append(f'<p>{_inline(chr(10).join(paragraph))}</p>')
    # Older or incomplete reports still get their supplied visual metadata. No
    # additional numbered chapter or independent sources chapter is introduced.
    output.extend(visual for chapter, visual in visuals.items() if chapter not in inserted)
    return "\n".join(output)


def _tone(item: Mapping, index: int = 0) -> str:
    return _TONES.get(_text(item.get("tone")), ("blue", "teal", "violet", "amber")[index % 4])


def _matrix(rows: list[Mapping]) -> str:
    cards = []
    for index, item in enumerate(rows):
        cards.append(
            f'<div class="matrix-card tone-{_tone(item, index)}">'
            f'<dt>{_escape(item.get("label"))}</dt><dd>{_escape(item.get("value"))}</dd></div>'
        )
    body = '<dl class="business-matrix">' + "".join(cards) + "</dl>" if cards else '<p class="empty">业务矩阵信息待补充。</p>'
    return f'<section class="visual matrix-visual" data-visual="matrix" aria-label="业务矩阵"><h3>业务矩阵</h3>{body}</section>'


def _journey(rows: list[Mapping]) -> str:
    stages = []
    for index, item in enumerate(rows):
        exception = _text(item.get("exception"))
        flag = f'<p class="exception-flag"><span>异常</span> {_escape(exception)}</p>' if exception else ""
        stages.append(
            f'<li class="journey-step tone-{_tone(item, index)}">'
            f'<span class="step-number">{index + 1:02d}</span><h4>{_escape(item.get("stage"))}</h4>'
            '<dl>'
            f'<dt>用户动作</dt><dd>{_escape(item.get("user"))}</dd>'
            f'<dt>系统响应</dt><dd>{_escape(item.get("system"))}</dd>'
            f'<dt>判断节点</dt><dd>{_escape(item.get("checkpoint"))}</dd>'
            f'</dl>{flag}</li>'
        )
    body = '<ol class="journey-flow">' + "".join(stages) + "</ol>" if stages else '<p class="empty">用户旅程信息待补充。</p>'
    return f'<section class="visual journey-visual" data-visual="journey" aria-label="用户旅程"><h3>用户旅程</h3>{body}</section>'


def _comparison(rows: list[Mapping], product_name: str) -> str:
    cells = "".join(
        f'<tr><th scope="row">{_escape(item.get("label"))}</th>'
        f'<td class="product-column">{_escape(item.get("product"))}</td>'
        f'<td>{_escape(item.get("alternatives"))}</td></tr>'
        for item in rows
    )
    body = (
        '<div class="table-scroll"><table class="comparison-table"><thead><tr>'
        f'<th scope="col">比较维度</th><th scope="col">{_escape(product_name)}</th>'
        f'<th scope="col">替代方案</th></tr></thead><tbody>{cells}</tbody></table></div>'
        if rows else '<p class="empty">替代方案比较信息待补充。</p>'
    )
    return f'<section class="visual comparison-visual" data-visual="comparison" aria-label="替代方案对比"><h3>替代方案对比</h3>{body}</section>'


def _insights(rows: list[Mapping]) -> str:
    cards = "".join(
        f'<article class="insight-callout tone-{_tone(item, index)}">'
        f'<span class="insight-number">PM · {index + 1:02d}</span>'
        f'<h4>{_escape(item.get("title"))}</h4><p>{_escape(item.get("body"))}</p></article>'
        for index, item in enumerate(rows)
    )
    body = f'<div class="insights-grid">{cards}</div>' if rows else '<p class="empty">PM 洞察待补充。</p>'
    return f'<section class="visual insights-visual" data-visual="insights" aria-label="PM 洞察"><h3>PM 洞察</h3>{body}</section>'


def _evidence(channels: list[Mapping], sources: list[Mapping]) -> str:
    coverage = []
    for channel in channels:
        error = _text(channel.get("error"))
        error_html = f'<p class="channel-error">{_escape(error)}</p>' if error else ""
        coverage.append(
            '<li class="channel-card">'
            f'<div class="channel-heading"><strong>{_escape(channel.get("name"))}</strong>'
            f'<span class="status-tag">{_escape(channel.get("status"))}</span></div>'
            f'<p class="channel-count">{_escape(channel.get("source_count", 0))} 条证据</p>'
            f'<p class="channel-query"><code>{_escape(channel.get("query"))}</code></p>{error_html}</li>'
        )
    evidence = []
    for source in sources:
        title = _text(source.get("title")) or _text(source.get("url")) or "未命名证据"
        evidence.append(
            '<li class="evidence-item">'
            f'<div><span class="source-id">{_escape(source.get("id"))}</span> '
            f'{_link(title, source.get("url"))}</div>'
            f'<p class="source-meta">{_escape(source.get("channel"))} · {_escape(source.get("source_type"))}</p>'
            f'<p class="source-snippet">{_escape(_text(source.get("snippet"))[:800])}</p></li>'
        )
    return (
        '<aside class="evidence-panel" aria-label="证据审计"><details>'
        f'<summary>证据覆盖与审计 <span>{len(channels)} 路渠道 · {len(sources)} 条证据</span></summary>'
        '<div class="evidence-body"><p class="audit-note">以下为检索状态与证据摘要，供核对报告依据。</p>'
        f'<ul class="channel-grid">{"".join(coverage)}</ul>'
        f'<ul class="evidence-list">{"".join(evidence)}</ul></div></details></aside>'
    )


_STYLE = """
:root{color-scheme:light;--ink:#18263d;--muted:#61718a;--line:#e2e8f1;--blue:#275dc2;--paper:#fff}
*{box-sizing:border-box}body{margin:0;background:#eef2f8;color:var(--ink);font:15px/1.8 system-ui,-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:#275dc2;text-decoration-thickness:1px;text-underline-offset:3px;overflow-wrap:anywhere}a:hover{color:#163d89}
.page-shell{max-width:1220px;margin:36px auto;padding:0 28px}.report{background:var(--paper);border:1px solid var(--line);border-radius:22px;box-shadow:0 16px 55px #233f7010;overflow:hidden}
.report-hero{padding:44px 48px 32px;background:linear-gradient(125deg,#eaf1ff,#f1edff 67%,#e9f8f4);border-bottom:1px solid var(--line)}
.eyebrow{font-size:12px;font-weight:800;letter-spacing:.16em;color:#4b65a3;margin:0 0 12px}.report-hero h1{font-size:clamp(28px,4vw,40px);line-height:1.3;margin:0 0 20px;letter-spacing:-.03em;overflow-wrap:anywhere}
.report-meta{display:flex;gap:12px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:13px}.meta-chip{padding:4px 12px;background:#ffffffb8;border:1px solid #ffffff;border-radius:20px}
.report-content{padding:22px 48px 48px}.report-content>h1,.report-content>h2{margin:36px 0 18px;line-height:1.45;letter-spacing:-.02em}.report-content>h2{padding-bottom:12px;border-bottom:2px solid var(--line);font-size:25px}.report-content>h3{font-size:19px;margin:28px 0 10px}
p{margin:12px 0;overflow-wrap:anywhere}li{overflow-wrap:anywhere}code{padding:2px 5px;border-radius:5px;background:#edf1f7;font-size:.88em;overflow-wrap:anywhere}pre{padding:18px;background:#f4f6fa;border:1px solid var(--line);border-radius:10px;overflow-x:auto}pre code{padding:0;background:transparent;white-space:pre}blockquote{margin:20px 0;border-left:4px solid #a1b7e4;padding:4px 20px;background:#f6f8fc;color:var(--muted)}hr{border:0;border-top:1px solid var(--line);margin:28px 0}
.visual{margin:22px 0 30px}.visual>h3{font-size:13px;letter-spacing:.08em;color:var(--muted);margin:0 0 12px}.empty{padding:18px;background:#f7f9fc;border:1px dashed #cbd5e1;border-radius:12px;color:var(--muted)}
.tone-blue{--accent:#315fc1;--tint:#edf3ff;--edge:#cbdaff}.tone-teal{--accent:#08776a;--tint:#e9f8f3;--edge:#bee9dd}.tone-violet{--accent:#7850b6;--tint:#f3edfd;--edge:#dfd0f5}.tone-amber{--accent:#946116;--tint:#fff5df;--edge:#f3ddaa}.tone-rose{--accent:#b14663;--tint:#fff0f3;--edge:#f4c7d1}
.business-matrix{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:0}.matrix-card{padding:20px 22px;background:var(--tint);border:1px solid var(--edge);border-top:4px solid var(--accent);border-radius:12px}.matrix-card dt{font-size:12px;font-weight:800;color:var(--accent);margin-bottom:8px}.matrix-card dd{margin:0;font-size:17px;font-weight:650;line-height:1.6;overflow-wrap:anywhere}
.journey-flow{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(180px,1fr);gap:18px;overflow-x:auto;padding:4px 2px 15px;margin:0;list-style:none}.journey-step{position:relative;min-width:0;padding:17px 15px;background:var(--tint);border:1px solid var(--edge);border-radius:12px}.journey-step:not(:last-child)::after{content:"→";position:absolute;right:-18px;top:36px;width:18px;text-align:center;color:#8391a9;font-size:18px;z-index:1}.step-number{font-size:12px;font-weight:900;color:var(--accent)}.journey-step h4{margin:6px 0 15px;color:var(--accent);font-size:16px;line-height:1.5}.journey-step dl{margin:0}.journey-step dt{font-size:11px;font-weight:800;color:var(--muted);margin:12px 0 3px}.journey-step dd{margin:0;font-size:12px;line-height:1.65;overflow-wrap:anywhere}.exception-flag{padding:8px 9px;margin:14px 0 0;background:#fff8f0;border:1px solid #efcea0;border-radius:6px;font-size:11px;color:#8a5214}.exception-flag span{font-weight:850}
.table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;margin:16px 0}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}th,td{padding:13px 16px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}thead th{background:#edf2fb;color:#385683;font-weight:800}tbody th{font-weight:650;background:#f8faff;min-width:110px}tbody tr:last-child>*{border-bottom:0}.product-column{background:#eef7ff}.comparison-table th:nth-child(2){background:#dcecff;color:#27558c}.comparison-table th:nth-child(3){background:#f0ebfa;color:#765497}.comparison-table{min-width:540px}.comparison-table td{width:38%}
.insights-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:14px}.insight-callout{padding:20px 22px;background:var(--tint);border:1px solid var(--edge);border-left:5px solid var(--accent);border-radius:10px}.insight-number{font-size:11px;letter-spacing:.08em;color:var(--accent);font-weight:850}.insight-callout h4{font-size:17px;line-height:1.5;margin:9px 0;color:var(--accent)}.insight-callout p{font-size:13px;margin:0}
.evidence-panel{margin-top:22px;background:#fff;border:1px solid var(--line);border-radius:14px;color:var(--muted)}summary{padding:18px 22px;cursor:pointer;font-size:14px;font-weight:700;color:#3a506f}summary span{font-size:12px;font-weight:450;margin-left:12px}.evidence-body{padding:0 22px 22px}.audit-note{font-size:12px}.channel-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;padding:0;list-style:none}.channel-card{padding:12px;background:#f7f9fc;border:1px solid var(--line);border-radius:9px;min-width:0}.channel-heading{display:flex;justify-content:space-between;gap:8px;align-items:start;color:#31496c;font-size:12px}.status-tag{font-size:10px;border:1px solid #d5dfed;padding:1px 6px;border-radius:10px;overflow-wrap:anywhere}.channel-count,.channel-query,.channel-error{font-size:11px;margin:7px 0 0}.channel-error{color:#9e4c3b}.channel-query code{background:transparent;padding:0}.evidence-list{list-style:none;padding:0;margin:20px 0 0}.evidence-item{padding:14px 0;border-top:1px solid var(--line);font-size:12px}.source-id{font-weight:800;font-size:10px;color:#657da1;background:#edf2fa;padding:3px 6px;border-radius:5px}.source-meta{font-size:10px;margin:5px 0}.source-snippet{font-size:12px;margin:4px 0 0}
@media(max-width:700px){.page-shell{margin:16px auto;padding:0 12px}.report-hero{padding:28px 22px 24px}.report-content{padding:12px 22px 28px}.business-matrix{grid-template-columns:1fr 1fr;gap:10px}.matrix-card{padding:14px}.matrix-card dd{font-size:14px}.insights-grid{grid-template-columns:1fr}summary span{display:block;margin:6px 0 0}}
@media(max-width:420px){.business-matrix{grid-template-columns:1fr}}
@media print{body{background:#fff}.page-shell{margin:0;max-width:none;padding:0}.report{box-shadow:none;border:0}.report-hero{padding:24px}.report-content{padding:10px 24px}.journey-flow{grid-auto-flow:initial;grid-template-columns:repeat(5,minmax(0,1fr));grid-auto-columns:auto;overflow:visible;gap:10px}.journey-step{padding:10px}.journey-step:not(:last-child)::after{right:-10px;width:10px;font-size:12px}.visual,.insight-callout{break-inside:avoid}.table-scroll{overflow:visible}.comparison-table{min-width:0}.evidence-panel{box-shadow:none}a{color:inherit}}
"""


def render_teardown(document: dict) -> str:
    """Return UTF-8-ready self-contained HTML; never access files or call a model."""
    metadata = document.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    product_name = _text(metadata.get("product_name")) or "AI 产品拆解报告"
    depth = _text(metadata.get("depth"))
    visuals = {
        1: _matrix(_items(metadata.get("matrix"))),
        2: _journey(_items(metadata.get("journey"))),
        5: _comparison(_items(metadata.get("comparison")), product_name),
        6: _insights(_items(metadata.get("insights"))),
    }
    content_html = _markdown(_text(document.get("content")), visuals)
    product_link = _link("访问产品", metadata.get("product_url")) if _safe_url(metadata.get("product_url")) else ""
    depth_html = f'<span class="meta-chip">{_escape(depth)}</span>' if depth else ""
    audit_html = _evidence(_items(metadata.get("channels")), _items(metadata.get("sources")))
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{_escape(_CSP)}">'
        f'<title>{_escape(product_name)} · 产品拆解报告</title><style>{_STYLE}</style></head>'
        '<body><div class="page-shell"><main class="report">'
        '<header class="report-hero"><p class="eyebrow">产品拆解 · 决策观察</p>'
        f'<h1>{_escape(product_name)}</h1><div class="report-meta">'
        f'{depth_html}{product_link}</div></header><article class="report-content">'
        f'{content_html}</article></main>{audit_html}</div></body></html>'
    )
