"""Tests for the BMF-Schreiben adapter."""

import io

from ingestion.bmf import (
    clean_pdf_text,
    extract_gz,
    extract_pdf,
    parse_sitemap,
    pdf_url_for,
)

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url>
  <loc>https://www.bundesfinanzministerium.de/Content/DE/Bilderstrecken/foo/eindruecke.html</loc>
  <lastmod>2025-09-22</lastmod>
</url>
<url>
  <loc>https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Steuerarten/Lohnsteuer/2025-12-05-steuerliche-behandlung-reisekosten-2026.html</loc>
  <lastmod>2025-12-05</lastmod>
</url>
<url>
  <loc>https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Internationales_Steuerrecht/Allgemeine_Informationen/2024-06-19-anwendung-PStTG.html</loc>
  <lastmod>2024-06-19</lastmod>
</url>
<url>
  <loc>https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Steuerarten/Umsatzsteuer/kein-datum-schreiben.html</loc>
</url>
</urlset>"""


def _make_pdf(text: str, title: str | None = None) -> bytes:
    """Build a minimal single-page PDF containing the given text."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if title:
        objs.append(f"<< /Title ({title}) >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_pos = out.tell()
    count = len(objs) + 1
    out.write(f"xref\n0 {count}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    trailer = f"trailer\n<< /Size {count} /Root 1 0 R"
    if title:
        trailer += f" /Info {len(objs)} 0 R"
    trailer += f" >>\nstartxref\n{xref_pos}\n%%EOF"
    out.write(trailer.encode())
    return out.getvalue()


class TestParseSitemap:
    def test_filters_and_sorts_newest_first(self):
        entries = parse_sitemap(SITEMAP_XML)
        assert len(entries) == 3
        assert entries[0]["slug"] == "2025-12-05-steuerliche-behandlung-reisekosten-2026"
        assert entries[0]["date"] == "2025-12-05"
        assert entries[1]["date"] == "2024-06-19"
        # Entry without a date in the filename sorts last
        assert entries[2]["slug"] == "kein-datum-schreiben"
        assert entries[2]["date"] == ""

    def test_extracts_category_and_lowercases_slug(self):
        entries = parse_sitemap(SITEMAP_XML)
        assert entries[0]["category"] == "Steuerarten/Lohnsteuer"
        assert entries[1]["slug"] == "2024-06-19-anwendung-psttg"
        assert entries[1]["category"] == "Internationales_Steuerrecht/Allgemeine_Informationen"

    def test_ignores_non_bmf_urls(self):
        slugs = {e["slug"] for e in parse_sitemap(SITEMAP_XML)}
        assert "eindruecke" not in slugs


class TestPdfUrlFor:
    def test_derives_pdf_url(self):
        html = "https://example.de/Content/DE/Downloads/BMF_Schreiben/x/2024-01-01-foo.html"
        assert pdf_url_for(html) == (
            "https://example.de/Content/DE/Downloads/BMF_Schreiben/x/"
            "2024-01-01-foo.pdf?__blob=publicationFile&v=1"
        )


class TestCleanPdfText:
    def test_drops_whitespace_lines_and_collapses(self):
        raw = " \n  \n \nBundesministerium  der Finanzen \n\n\n\nText   hier \n"
        assert clean_pdf_text(raw) == "Bundesministerium der Finanzen\n\nText hier"


class TestExtractGz:
    def test_finds_gz_in_letterhead(self):
        text = "Betreff: Foo\nGZ: IV C 5 - S 2353/00094/007/012\nDOK: 123\n"
        assert extract_gz(text) == "IV C 5 - S 2353/00094/007/012"

    def test_missing_gz(self):
        assert extract_gz("kein Geschaeftszeichen") == ""


class TestExtractPdf:
    def test_extracts_title_and_text(self):
        pdf = _make_pdf("Umsatzsteuer Anwendung Test", title="Test Titel")
        title, text = extract_pdf(pdf)
        assert title == "Test Titel"
        assert "Umsatzsteuer Anwendung Test" in text

    def test_no_title_metadata(self):
        pdf = _make_pdf("Inhalt ohne Titel")
        title, text = extract_pdf(pdf)
        assert title == ""
        assert "Inhalt ohne Titel" in text

    def test_invalid_pdf_returns_empty(self):
        title, text = extract_pdf(b"definitely not a pdf")
        assert (title, text) == ("", "")
