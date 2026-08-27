import unittest

from creel.core.urlnorm import canonicalize, registrable_domain


class TestCanonicalize(unittest.TestCase):
    def test_50_utm_variants_collapse_to_one_key(self):
        base = "https://example.com/article"
        variants = [
            f"{base}?utm_source=fixture&utm_campaign=t{i}&utm_medium=email"
            for i in range(50)
        ]
        keys = {canonicalize(v) for v in variants}
        self.assertEqual(len(keys), 1, "all utm-tagged variants must canonicalize identically")
        self.assertEqual(canonicalize(base), canonicalize(variants[0]))

    def test_www_and_scheme_and_trailing_slash(self):
        self.assertEqual(
            canonicalize("https://WWW.Example.com/path/"),
            canonicalize("https://example.com/path"),
        )

    def test_default_port_stripped(self):
        self.assertEqual(
            canonicalize("https://example.com:443/x"),
            canonicalize("https://example.com/x"),
        )

    def test_non_default_port_kept(self):
        self.assertNotEqual(
            canonicalize("https://example.com:8443/x"),
            canonicalize("https://example.com/x"),
        )

    def test_non_tracking_query_param_preserved(self):
        self.assertNotEqual(
            canonicalize("https://example.com/x?id=1"),
            canonicalize("https://example.com/x?id=2"),
        )


class TestRegistrableDomain(unittest.TestCase):
    def test_subdomain_collapses_to_registrable_suffix(self):
        self.assertEqual(
            registrable_domain("https://cdn123.assets.example.com/a"),
            registrable_domain("https://example.com/b"),
        )

    def test_distinct_domains_differ(self):
        self.assertNotEqual(
            registrable_domain("https://example.com/a"),
            registrable_domain("https://other.com/a"),
        )


if __name__ == "__main__":
    unittest.main()
