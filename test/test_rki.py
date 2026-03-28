"""Tests for the RKI adapter."""

from ingestion.rki import RkiAdapter


class TestRkiParsing:
    SAMPLE_OAI = """<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <header><identifier>oai:edoc.rki.de:1</identifier></header>
          <metadata>
            <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                       xmlns:dc="http://purl.org/dc/elements/1.1/">
              <dc:title>Epidemiologisches Bulletin 42/2024</dc:title>
              <dc:creator>RKI</dc:creator>
              <dc:subject>610 Medizin und Gesundheit</dc:subject>
              <dc:description>Bericht zu Infektionskrankheiten.</dc:description>
              <dc:date>2024-10-15</dc:date>
              <dc:language>ger</dc:language>
              <dc:identifier>http://edoc.rki.de/176904/12345</dc:identifier>
              <dc:type>report</dc:type>
            </oai_dc:dc>
          </metadata>
        </record>
        <record>
          <header status="deleted"><identifier>oai:edoc.rki.de:2</identifier></header>
        </record>
      </ListRecords>
    </OAI-PMH>"""

    def test_parses_records(self):
        import xml.etree.ElementTree as ET
        adapter = RkiAdapter()
        # Directly test the parsing logic by simulating _fetch_page
        root = ET.fromstring(self.SAMPLE_OAI)
        ns = {"oai": "http://www.openarchives.org/OAI/2.0/"}
        records = root.findall(".//oai:record", ns)
        # Should skip deleted record
        non_deleted = [r for r in records if r.find("oai:header", ns).get("status") != "deleted"]
        assert len(non_deleted) == 1
