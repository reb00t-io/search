"""Tests for the gesetze-im-internet.de adapter."""

from ingestion.gesetze import PRIORITY_LAWS, _parse_law_xml, _toc_priority, _xml_text, sort_toc
import xml.etree.ElementTree as ET


class TestXmlText:
    def test_simple(self):
        el = ET.fromstring("<p>Hello world</p>")
        assert _xml_text(el) == "Hello world"

    def test_nested(self):
        el = ET.fromstring("<p>Hello <b>bold</b> world</p>")
        assert _xml_text(el) == "Hello bold world"

    def test_none(self):
        assert _xml_text(None) == ""


class TestParseLawXml:
    def test_parses_basic_law(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <dokumente>
          <metadaten>
            <jurabk>TestG</jurabk>
            <langue>Testgesetz</langue>
          </metadaten>
          <norm>
            <metadaten>
              <enbez>§ 1</enbez>
              <titel>Anwendungsbereich</titel>
            </metadaten>
            <textdaten>
              <text>
                <Content>
                  <P>Dieses Gesetz regelt den Anwendungsbereich für Testzwecke.</P>
                </Content>
              </text>
            </textdaten>
          </norm>
        </dokumente>"""
        sections = _parse_law_xml(xml)
        assert len(sections) == 1
        assert sections[0]["section_num"] == "§ 1"
        assert sections[0]["section_title"] == "Anwendungsbereich"
        assert sections[0]["law_abbrev"] == "TestG"
        assert "Anwendungsbereich" in sections[0]["text"]

    def test_skips_empty_sections(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <dokumente>
          <metadaten><jurabk>X</jurabk></metadaten>
          <norm>
            <metadaten><enbez>§ 1</enbez></metadaten>
            <textdaten><text><Content><P>x</P></Content></text></textdaten>
          </norm>
        </dokumente>"""
        sections = _parse_law_xml(xml)
        assert len(sections) == 0  # too short (<20 chars)

    def test_handles_invalid_xml(self):
        sections = _parse_law_xml("not xml at all")
        assert sections == []


class TestTocPriority:
    def test_priority_law_ranks_before_unlisted(self):
        estg = "http://www.gesetze-im-internet.de/estg/xml.zip"
        other = "http://www.gesetze-im-internet.de/zzz-verordnung/xml.zip"
        assert _toc_priority(estg) < _toc_priority(other)

    def test_slug_with_year_suffix_matches(self):
        ao = "http://www.gesetze-im-internet.de/ao_1977/xml.zip"
        assert _toc_priority(ao) < len(PRIORITY_LAWS)

    def test_prefix_without_underscore_does_not_match(self):
        # "aok..." must not match the "ao" priority entry
        aok = "http://www.gesetze-im-internet.de/aokweitg/xml.zip"
        assert _toc_priority(aok) == len(PRIORITY_LAWS)

    def test_sort_toc_moves_priority_laws_first(self):
        entries = [
            {"title": "A-Verordnung", "link": "http://www.gesetze-im-internet.de/averordnung/xml.zip"},
            {"title": "Handelsgesetzbuch", "link": "http://www.gesetze-im-internet.de/hgb/xml.zip"},
            {"title": "Abgabenordnung", "link": "http://www.gesetze-im-internet.de/ao_1977/xml.zip"},
        ]
        ordered = sort_toc(entries)
        # HGB is listed before AO in PRIORITY_LAWS; unlisted law comes last
        assert [e["title"] for e in ordered] == [
            "Handelsgesetzbuch", "Abgabenordnung", "A-Verordnung",
        ]
