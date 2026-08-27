import unittest

from creel.core.dispatch import ContentClass, classify_content


class TestClassifyContent(unittest.TestCase):
    def test_pdf_by_content_type(self):
        self.assertEqual(classify_content("https://x.com/a", "application/pdf"), ContentClass.PDF)

    def test_pdf_by_extension_when_no_content_type(self):
        self.assertEqual(classify_content("https://x.com/report.pdf"), ContentClass.PDF)

    def test_rss_feed(self):
        self.assertEqual(classify_content("https://x.com/feed", "application/rss+xml"), ContentClass.XML_FEED)

    def test_json_by_extension(self):
        self.assertEqual(classify_content("https://x.com/data.json"), ContentClass.JSON)

    def test_defaults_to_html(self):
        self.assertEqual(classify_content("https://x.com/some/page"), ContentClass.HTML)

    def test_html_content_type_variant(self):
        self.assertEqual(classify_content("https://x.com/a", "text/html; charset=utf-8"), ContentClass.HTML)


if __name__ == "__main__":
    unittest.main()
