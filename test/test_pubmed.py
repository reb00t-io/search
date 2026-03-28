"""Tests for the PubMed adapter."""

from ingestion.pubmed import PubmedAdapter


class TestParsePubmedXml:
    SAMPLE_XML = """<?xml version="1.0"?>
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>12345678</PMID>
          <Article>
            <ArticleTitle>A Study on Machine Learning in Medicine</ArticleTitle>
            <Abstract>
              <AbstractText Label="BACKGROUND">ML is widely used.</AbstractText>
              <AbstractText Label="METHODS">We reviewed 100 studies.</AbstractText>
              <AbstractText Label="CONCLUSIONS">ML improves outcomes.</AbstractText>
            </Abstract>
            <AuthorList>
              <Author><LastName>Smith</LastName><ForeName>John</ForeName></Author>
              <Author><LastName>Lee</LastName><ForeName>Jane</ForeName></Author>
            </AuthorList>
            <Journal><Title>Nature Medicine</Title></Journal>
            <ELocationID EIdType="doi">10.1234/test</ELocationID>
          </Article>
          <MeshHeadingList>
            <MeshHeading><DescriptorName>Machine Learning</DescriptorName></MeshHeading>
          </MeshHeadingList>
          <KeywordList>
            <Keyword>AI</Keyword>
            <Keyword>healthcare</Keyword>
          </KeywordList>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>"""

    def test_parses_article(self):
        adapter = PubmedAdapter()
        articles = adapter._parse_articles_xml(self.SAMPLE_XML)
        assert len(articles) == 1
        a = articles[0]
        assert a["pmid"] == "12345678"
        assert "Machine Learning" in a["title"]
        assert a["authors"] == ["John Smith", "Jane Lee"]
        assert a["journal"] == "Nature Medicine"
        assert a["doi"] == "10.1234/test"
        assert "Machine Learning" in a["mesh_terms"]
        assert "AI" in a["keywords"]

    def test_abstract_has_sections(self):
        adapter = PubmedAdapter()
        articles = adapter._parse_articles_xml(self.SAMPLE_XML)
        abstract = articles[0]["abstract"]
        assert "**BACKGROUND:**" in abstract
        assert "**METHODS:**" in abstract
        assert "**CONCLUSIONS:**" in abstract

    def test_handles_invalid_xml(self):
        adapter = PubmedAdapter()
        articles = adapter._parse_articles_xml("not xml")
        assert articles == []

    def test_skips_articles_without_abstract(self):
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>99999</PMID>
              <Article>
                <ArticleTitle>No Abstract Here</ArticleTitle>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>"""
        adapter = PubmedAdapter()
        articles = adapter._parse_articles_xml(xml)
        assert len(articles) == 0
