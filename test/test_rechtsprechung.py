"""Tests for the rechtsprechung-im-internet.de adapter."""

import xml.etree.ElementTree as ET

from ingestion.rechtsprechung import (
    _block_text,
    decision_url,
    parse_decision_xml,
    parse_toc,
    sort_toc,
)

DECISION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
   <doknr>JURE100054597</doknr>
   <ecli>ECLI:DE:BVERWG:2010:080110B9B3.09.0</ecli>
   <gertyp>BVerwG</gertyp>
   <gerort/>
   <spruchkoerper>9. Senat</spruchkoerper>
   <entsch-datum>20100108</entsch-datum>
   <aktenzeichen>9 B 3/09</aktenzeichen>
   <doktyp>Beschluss</doktyp>
   <titelzeile/>
   <leitsatz/>
   <tenor>
      <div>
         <dl class="RspDL">
            <dt/>
            <dd><p style="margin-left:36pt">Die Beschwerde wird verworfen.</p></dd>
         </dl>
      </div>
   </tenor>
   <tatbestand/>
   <entscheidungsgruende/>
   <gruende>
      <div>
         <dl class="RspDL">
            <dt><a name="rd_1">1</a></dt>
            <dd><p>Die Beschwerde ist als unzulässig zu verwerfen. Sie wurde
            verspätet begründet.</p></dd>
         </dl>
      </div>
   </gruende>
</dokument>"""

TOC_XML = """<?xml version='1.0' encoding='UTF-8'?>
<items>
<item>
 <gericht>BVerwG 9. Senat</gericht>
 <entsch-datum>20100108</entsch-datum>
 <aktenzeichen>9 B 3/09</aktenzeichen>
 <link>http://www.rechtsprechung-im-internet.de/jportal/docs/bsjrs/jb-JURE100054597.zip</link>
 <modified>2026-04-14T21:14:20.772Z</modified>
</item>
<item>
 <gericht>BFH 3. Senat</gericht>
 <entsch-datum>20200114</entsch-datum>
 <aktenzeichen>III R 3/19</aktenzeichen>
 <link>http://www.rechtsprechung-im-internet.de/jportal/docs/bsjrs/jb-STRE202010001.zip</link>
 <modified>2026-04-14T21:14:20.772Z</modified>
</item>
<item>
 <gericht>BGH 9. Zivilsenat</gericht>
 <entsch-datum>20250601</entsch-datum>
 <aktenzeichen>IX ZB 72/08</aktenzeichen>
 <link>http://www.rechtsprechung-im-internet.de/jportal/docs/bsjrs/jb-JURE100055033.zip</link>
 <modified>2025-06-23T21:55:54.378Z</modified>
</item>
</items>"""


class TestBlockText:
    def test_none(self):
        assert _block_text(None) == ""

    def test_collapses_whitespace_in_paragraphs(self):
        el = ET.fromstring("<div><p>Hello\n   world</p><p>Second   para</p></div>")
        assert _block_text(el) == "Hello world\n\nSecond para"

    def test_skips_dt_margin_numbers(self):
        el = ET.fromstring('<div><dl><dt><a name="rd_1">1</a></dt><dd><p>Text.</p></dd></dl></div>')
        text = _block_text(el)
        assert "Text." in text
        assert "1" not in text

    def test_br_becomes_line_break(self):
        el = ET.fromstring("<p>eins<br/>zwei</p>")
        assert _block_text(el) == "eins\n\nzwei"


class TestParseDecisionXml:
    def test_parses_sample_decision(self):
        decision = parse_decision_xml(DECISION_XML)
        assert decision is not None
        assert decision["doknr"] == "JURE100054597"
        assert decision["court"] == "BVerwG"
        assert decision["aktenzeichen"] == "9 B 3/09"
        assert decision["date"] == "2010-01-08"
        assert decision["title"] == "BVerwG Beschluss vom 2010-01-08 — 9 B 3/09"
        assert "## Tenor" in decision["text"]
        assert "Die Beschwerde wird verworfen." in decision["text"]
        assert "## Gründe" in decision["text"]
        # Empty sections are omitted
        assert "## Leitsatz" not in decision["text"]
        assert "## Tatbestand" not in decision["text"]

    def test_rejects_empty_document(self):
        xml = "<dokument><doknr>X1</doknr><gertyp>BGH</gertyp></dokument>"
        assert parse_decision_xml(xml) is None

    def test_rejects_invalid_xml(self):
        assert parse_decision_xml("not xml") is None

    def test_rejects_missing_doknr(self):
        xml = "<dokument><gertyp>BGH</gertyp><tenor><p>Text hier.</p></tenor></dokument>"
        assert parse_decision_xml(xml) is None


class TestToc:
    def test_parse_toc(self):
        entries = parse_toc(TOC_XML)
        assert len(entries) == 3
        assert entries[0]["court"] == "BVerwG"
        assert entries[0]["doknr"] == "JURE100054597"
        assert entries[1]["date"] == "20200114"

    def test_sort_toc_bfh_first_then_newest(self):
        entries = sort_toc(parse_toc(TOC_XML))
        # BFH has priority even though its decision is older than the BGH one
        assert entries[0]["court"] == "BFH"
        # Remaining courts sorted newest first
        assert entries[1]["court"] == "BGH"
        assert entries[2]["court"] == "BVerwG"


class TestDecisionUrl:
    def test_url_contains_doknr(self):
        url = decision_url("JURE100054597")
        assert "docid=JURE100054597" in url
        assert url.startswith("https://www.rechtsprechung-im-internet.de/")
