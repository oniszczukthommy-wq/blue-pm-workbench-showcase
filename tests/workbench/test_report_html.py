from html.parser import HTMLParser

import pytest

from backend.report_html import render_teardown


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.closed = []
        self.data = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        self.closed.append(tag)

    def handle_data(self, data):
        self.data.append(data)


def sample_document():
    return {
        "content": "\n\n".join(
            f"## {number}. {title}\n\n这是 **产品观察**，请看 [官网](https://product.example/about) 和 `API`。"
            for number, title in enumerate(
                ["结论", "业务与价值", "用户旅程", "体验", "商业化", "竞品比较", "PM 洞察"]
            )
        ),
        "metadata": {
            "product_name": "测试产品",
            "product_url": "https://product.example",
            "depth": "深度拆解",
            "matrix": [
                {"label": "用户", "value": "产品经理", "tone": "blue"},
                {"label": "需求", "value": "缩短调研时间", "tone": "teal"},
                {"label": "价值", "value": "有来源的判断", "tone": "violet"},
            ],
            "journey": [
                {
                    "stage": stage,
                    "user": f"动作 {index}",
                    "system": f"响应 {index}",
                    "checkpoint": f"判断 {index}",
                    "exception": "额度不足" if index == 3 else "",
                }
                for index, stage in enumerate(["进入", "输入", "生成", "校验", "导出"], 1)
            ],
            "comparison": [{"label": "证据", "product": "保留原链接", "alternatives": "手工搜集"}],
            "insights": [{"title": "先把证据留住", "body": "让判断可以复核。", "tone": "amber"}],
            "sources": [{
                "id": "S1", "title": "产品文档", "url": "https://docs.product.example/intro",
                "channel": "官方资料", "snippet": "文档证据摘要", "source_type": "documentation",
                "content": "FULL_SOURCE_BODY_MUST_NOT_BE_EMBEDDED",
            }],
            "channels": [{
                "id": "official", "name": "官方资料", "status": "success",
                "query": "测试产品 site:product.example", "source_count": 1,
            }],
        },
    }


def test_visuals_are_inside_matching_chapters_and_journey_is_horizontal():
    rendered = render_teardown(sample_document())
    for chapter, visual in [(1, "matrix"), (2, "journey"), (5, "comparison"), (6, "insights")]:
        heading = rendered.index(f'id="chapter-{chapter}"')
        following = rendered.find(f'id="chapter-{chapter + 1}"')
        position = rendered.index(f'data-visual="{visual}"')
        assert heading < position
        assert following < 0 or position < following
    page = Page(rendered)
    assert len([attrs for tag, attrs in page.tags if attrs.get("class", "").startswith("journey-step ")]) == 5
    assert 'grid-auto-flow:column' in rendered
    assert 'grid-auto-columns:minmax(180px,1fr)' in rendered
    assert 'overflow-x:auto' in rendered
    assert "exception-flag" in rendered and "额度不足" in rendered
    assert "business-matrix" in rendered and "comparison-table" in rendered and "insight-callout" in rendered
    assert 'tone-blue' in rendered and 'tone-teal' in rendered and 'tone-violet' in rendered


def test_basic_markdown_is_rendered_without_raw_html_or_image_loading():
    document = sample_document()
    document["content"] = """# 总览

段落含 **粗体** 与 `inline <code>`，以及 [安全链接](https://example.com?a=1&b=2)。

| 比较维度 | 观察 |
| :--- | ---: |
| 使用门槛 | **低** |

- 第一条
- 第二条

1. 步骤一
2. 步骤二

> 需进一步核实。

```html
<script>alert(1)</script>
```

<img src="https://tracking.example/pixel" onerror="alert(1)">
![图片](https://tracking.example/second)
"""
    rendered = render_teardown(document)
    page = Page(rendered)
    tags = [tag for tag, _ in page.tags]
    assert {"h1", "p", "strong", "code", "table", "thead", "tbody", "ul", "ol", "li", "blockquote", "pre"} <= set(tags)
    assert "<strong>粗体</strong>" in rendered
    assert "<code>inline &lt;code&gt;</code>" in rendered
    assert any(tag == "a" and attrs.get("href") == "https://example.com?a=1&b=2" for tag, attrs in page.tags)
    assert "<strong>低</strong>" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "script" not in tags and "img" not in tags


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///etc/passwd",
    "//evil.example", "https://user:password@evil.example", "https://user@evil.example",
    "https://good.example\\@evil.example", "https://good.example\n.evil.example",
    "https://good.example\t", "https://good.example:invalid", "https://", "http://[",
    'https://good.example/\" onclick=\"alert(1)',
])
def test_unsafe_urls_are_never_links(url):
    document = sample_document()
    document["metadata"]["product_url"] = url
    document["metadata"]["sources"][0]["url"] = url
    document["content"] = f"[不安全链接]({url})"
    page = Page(render_teardown(document))
    assert not [attrs for tag, attrs in page.tags if tag == "a"]


def test_all_dynamic_fields_are_escaped_and_tones_cannot_inject_markup():
    attack = '<script>evil()</script><img src=x onerror="evil()">&"'
    document = sample_document()
    document["content"] = f"## 1. {attack}\n\n{attack}\n\n**{attack}**"
    document["metadata"]["product_name"] = attack
    document["metadata"]["depth"] = attack
    for key in ("matrix", "journey", "comparison", "insights", "channels", "sources"):
        for row in document["metadata"][key]:
            for field in list(row):
                row[field] = attack
    rendered = render_teardown(document)
    page = Page(rendered)
    assert "&lt;script&gt;evil()&lt;/script&gt;" in rendered
    assert "<script>" not in rendered and "<img " not in rendered
    assert not any(tag in {"script", "img", "iframe", "object", "embed", "svg", "form"} for tag, _ in page.tags)
    assert all(not name.startswith("on") for _, attrs in page.tags for name in attrs)
    assert not any('"' in attrs.get("class", "") for _, attrs in page.tags)
    assert rendered.count('data-visual="matrix"') == 1


def test_evidence_panel_is_outside_report_and_does_not_repeat_source_body():
    rendered = render_teardown(sample_document())
    assert rendered.index("</main>") < rendered.index('<aside class="evidence-panel"')
    assert "<details>" in rendered and "证据覆盖与审计" in rendered
    assert "文档证据摘要" in rendered and "测试产品 site:product.example" in rendered
    assert "FULL_SOURCE_BODY_MUST_NOT_BE_EMBEDDED" not in rendered
    assert "来源列表</h" not in rendered and "参考来源</h" not in rendered
    assert rendered.count('id="chapter-') == 7


def test_no_external_resources_and_csp_blocks_script_execution():
    rendered = render_teardown(sample_document())
    page = Page(rendered)
    csp = next(attrs["content"] for tag, attrs in page.tags
               if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy")
    assert "default-src 'none'" in csp
    assert "style-src 'unsafe-inline'" in csp
    assert "base-uri 'none'" in csp and "form-action 'none'" in csp
    assert any(tag == "meta" and attrs.get("charset") == "utf-8" for tag, attrs in page.tags)
    assert not any(tag in {"script", "link", "img", "iframe", "object", "embed", "audio", "video"} for tag, _ in page.tags)
    assert not any("src" in attrs or "srcset" in attrs for _, attrs in page.tags)
    assert "@import" not in rendered and "url(" not in rendered
    for tag, attrs in page.tags:
        if tag == "a":
            assert attrs["href"].startswith(("http://", "https://"))
            assert "noreferrer" in attrs["rel"].split()


def test_missing_metadata_and_numbered_chapter_variants_are_supported():
    minimal = render_teardown({"content": "报告正文", "metadata": None})
    assert "报告正文" in minimal and "业务矩阵信息待补充" in minimal
    assert all(f'data-visual="{name}"' in minimal for name in ("matrix", "journey", "comparison", "insights"))
    document = sample_document()
    document["content"] = "## 第１章 业务\n\n## 2、旅程\n\n### 2.1 补充\n\n## 5：比较\n\n## 6 洞察"
    rendered = render_teardown(document)
    assert rendered.count('id="chapter-2"') == 1
    assert rendered.count('data-visual="journey"') == 1
    assert rendered.index('id="chapter-1"') < rendered.index('data-visual="matrix"') < rendered.index('id="chapter-2"')


def test_source_summaries_are_bounded_and_invalid_metadata_rows_are_ignored():
    document = sample_document()
    document["metadata"]["sources"][0]["snippet"] = "x" * 800 + "OMITTED_LONG_TAIL"
    document["metadata"]["matrix"].extend([None, "not a row"])
    rendered = render_teardown(document)
    assert "OMITTED_LONG_TAIL" not in rendered
    assert "x" * 800 in rendered
    assert "not a row" not in rendered
