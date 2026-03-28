"""Tests for the RSS adapter."""

from ingestion.rss_adapter import _html_to_markdown, _parse_rss_items


class TestParseRssItems:
    SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel>
        <title>Test Feed</title>
        <item>
          <title>Article One</title>
          <link>https://example.com/article-1</link>
          <description>Short description.</description>
          <pubDate>Fri, 28 Mar 2025 10:00:00 +0100</pubDate>
          <category>Politik</category>
        </item>
        <item>
          <title>Article Two</title>
          <link>https://example.com/article-2</link>
          <description>&lt;p&gt;HTML description.&lt;/p&gt;</description>
          <content:encoded>&lt;p&gt;Full article &lt;strong&gt;content&lt;/strong&gt; here.&lt;/p&gt;</content:encoded>
        </item>
      </channel>
    </rss>"""

    def test_parses_items(self):
        items = _parse_rss_items(self.SAMPLE_RSS)
        assert len(items) == 2
        assert items[0]["title"] == "Article One"
        assert items[0]["link"] == "https://example.com/article-1"
        assert items[0]["category"] == "Politik"

    def test_content_encoded(self):
        items = _parse_rss_items(self.SAMPLE_RSS)
        assert "Full article" in items[1]["content"]

    def test_handles_invalid_xml(self):
        items = _parse_rss_items("not xml")
        assert items == []


class TestHtmlToMarkdown:
    def test_strips_scripts(self):
        html = "<p>Hello</p><script>alert('x')</script><p>World</p>"
        md = _html_to_markdown(html)
        assert "alert" not in md
        assert "Hello" in md
        assert "World" in md

    def test_converts_bold(self):
        md = _html_to_markdown("<p>This is <strong>important</strong>.</p>")
        assert "**important**" in md

    def test_empty_input(self):
        assert _html_to_markdown("") == ""
        assert _html_to_markdown(None) == ""
