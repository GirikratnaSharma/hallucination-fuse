import unittest
from hallucination_fuse.metrics import evaluate
from hallucination_fuse.parser import Claim, Source, Finding, TrustReport, parse_text


def report(claims):
    return TrustReport("test", "test", " ".join(c.text for c in claims), "Reviewed", 0,
                       dict.fromkeys(("Accuracy", "Bias", "Tone", "Agreement"), 5),
                       {}, claims, [Finding("Sentiment", "Sentiment", "NOTE", "Neutral")], "Findings\n")


class MetricsTests(unittest.TestCase):
    def test_empty_is_unknown_and_held(self):
        result = evaluate(parse_text(""))
        self.assertIsNone(result.metrics["evidence_readiness"].value)
        self.assertIsNone(result.metrics["supported_share"].value)
        self.assertTrue(result.holds)

    def test_uncertainty_and_contradiction_are_distinct(self):
        r = report([Claim(1, "Sales will rise.", "Unverified"), Claim(2, "Costs will fall.", "Contradicted")])
        m = evaluate(r, {1: 3, 2: 1}).metrics
        self.assertEqual(m["uncertainty_share"].value, .75)
        self.assertEqual(m["contradiction_share"].value, .25)

    def test_missing_sources_cannot_earn_readiness(self):
        m = evaluate(report([Claim(1, "Costs fell.", "Supported")])).metrics
        self.assertEqual(m["evidence_readiness"].value, 0)

    def test_duplicate_citations_do_not_boost_quality(self):
        c = Claim(1, "Costs fell.", "Supported", sources=[Source("News", "Study")])
        before = evaluate(report([c])).metrics["source_metadata_quality"].value
        c.sources *= 10
        self.assertEqual(evaluate(report([c])).metrics["source_metadata_quality"].value, before)

    def test_unchecked_text_lowers_readiness(self):
        r = report([Claim(1, "Costs fell.", "Supported", sources=[Source("Official", "Study")])])
        self.assertEqual(evaluate(r).metrics["evidence_readiness"].value, 1)
        r.analyzed_text += " Revenue doubled."
        result = evaluate(r)
        self.assertEqual(result.metrics["exact_sentence_coverage"].value, .5)
        self.assertEqual(result.metrics["evidence_readiness"].value, .5)
        self.assertTrue(result.holds)

    def test_invalid_weights_rejected(self):
        r = report([Claim(1, "Costs fell.", "Supported")])
        for weights in ({1: -1}, {1: float("nan")}, {2: 1}):
            with self.assertRaises(ValueError):
                evaluate(r, weights)

    def test_unknown_verdict_is_uncertainty(self):
        result = evaluate(report([Claim(1, "Costs fell.", "New verdict")]))
        self.assertEqual(result.metrics["uncertainty_share"].value, 1)


if __name__ == "__main__":
    unittest.main()
