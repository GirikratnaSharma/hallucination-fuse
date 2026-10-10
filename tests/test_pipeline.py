import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hallucination_fuse import Fuse, FuseTripped, audit, human
from hallucination_fuse.fuse import decide
from hallucination_fuse.parser import Check, Source, parse_text, parse_report
from hallucination_fuse.trust import CONTEXTS, claim_class

ROOT = Path(__file__).resolve().parents[1]
CLEAN = '''Analyzed text
The survey included 100 participants.
Verdict
Supported
Issues found: 0
Module Scores
Accuracy: █████
Bias: █████
Tone: █████
Agreement: █████
Claim analysis
Claim 1
The survey included 100 participants.
Verdict: Supported
● Amount: matches (Source 1)
Sources:
● 1 Official · Survey — Published: 2026-10-01
Findings
Sentiment (NOTE)
Neutral phrasing.
Methodology
'''


class PipelineTests(unittest.TestCase):
    def test_empty_blocks_in_every_context(self):
        for context in CONTEXTS:
            d = decide(parse_text(''), context)
            self.assertEqual(d.action, 'BLOCK')
            self.assertTrue(d.tripped)

    def test_missing_findings_blocks(self):
        self.assertEqual(decide(parse_text(CLEAN.split('Findings')[0]), 'internal_memo').action, 'BLOCK')

    def test_missing_citations_holds_even_internal_use(self):
        r = parse_text(CLEAN)
        r.claims[0].sources = []
        r.claims[0].checks = []
        self.assertEqual(decide(r, 'internal_memo').action, 'PULL_SOURCE')

    def test_real_pdf_decisions(self):
        expected = {'nuclear': ('REWRITE', 0), 'wfh': ('ESCALATE', 0)}
        for name, (action, adverse) in expected.items():
            r = parse_report(str(ROOT / 'reports' / (name + '.pdf')))
            self.assertEqual((len(r.claims), len(r.findings)), (5, 4))
            self.assertTrue(all(c.sources and c.checks for c in r.claims))
            d = decide(r, 'comms_publish')
            self.assertEqual(d.action, action)
            self.assertTrue(d.tripped)
            self.assertEqual(len(d.targets['contradicted']), adverse)
            self.assertTrue(any('/ Source ' in reason.ref for reason in d.reasons))
        self.assertEqual(r.score_notes['Accuracy'], 'Not scored')

    def test_legacy_fixture_still_parses(self):
        r = parse_report(str(ROOT / 'tests/fixtures/synthetic_wfh.txt'))
        self.assertEqual((len(r.claims), len(r.findings)), (3, 4))
        self.assertEqual(r.scores['Accuracy'], 3)

    def test_subchecks_can_reduce_but_not_raise_support(self):
        c = parse_text(CLEAN).claims[0]
        for status, expected in [('contradicts', 'contradicted'), ('not_found', 'unverified'), ('different', 'partial')]:
            c.checks = [Check('Amount', status, 'detail', 1)]
            self.assertEqual(claim_class(c), expected)
        c.verdict = 'Not enough evidence'
        c.checks = [Check('Amount', 'match', 'detail', 1)]
        self.assertEqual(claim_class(c), 'unverified')

    def test_dangling_source_reference_is_unknown(self):
        c = parse_text(CLEAN).claims[0]
        c.checks[0].source_idx = 99
        self.assertEqual(claim_class(c), 'unverified')

    def test_invented_source_cannot_approve(self):
        r = parse_text(CLEAN.replace('Verdict: Supported', 'Verdict: Not enough evidence'))
        d = decide(r, 'comms_publish')
        new, _, _ = human.apply_response(r, d, 'source 1 Official Completely invented source')
        self.assertTrue(new.tripped)
        self.assertEqual(new.trust_low, d.trust_low)
        self.assertEqual(r.claims[0].verdict, 'Not enough evidence')
        self.assertEqual(len(r.claims[0].pending_sources), 1)

    def test_review_does_not_mutate_original_or_release_rewrite(self):
        r = parse_report(str(ROOT / 'tests/fixtures/synthetic_wfh.txt'))
        d = decide(r, 'comms_publish')
        new, _, _ = human.apply_response(r, d, 'rewrite')
        self.assertEqual(d.action, 'BLOCK')
        self.assertEqual(new.action, 'REWRITE')
        self.assertTrue(new.tripped)
        approved, _, _ = human.apply_response(r, d, 'approve I accept this risk')
        self.assertTrue(approved.tripped)

    def test_bad_human_quoting_is_safe(self):
        r = parse_text(CLEAN)
        d = decide(r, 'comms_publish')
        new, _, final = human.apply_response(r, d, 'source "')
        self.assertIs(new, d)
        self.assertFalse(final)

    def test_guard_binds_report_payload_and_stops_changed_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'report.txt'
            path.write_text(CLEAN)
            calls = []
            @Fuse('comms_publish', log=False).guard(str(path))
            def publish(payload):
                calls.append(payload)
                return 'published'
            self.assertEqual(publish('The survey included 100 participants.'), 'published')
            with self.assertRaises(FuseTripped):
                publish('The survey included 100000 participants.')
            self.assertEqual(len(calls), 1)
            path.write_text('')
            with self.assertRaises(FuseTripped):
                publish('')
            self.assertEqual(len(calls), 1)

    def test_guard_rejects_unbound_tool_parameters(self):
        with self.assertRaises(ValueError):
            Fuse().guard('unused')(lambda payload, destination: None)

    def test_guard_records_execution_and_report_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'report.txt'
            path.write_text(CLEAN)
            with patch('hallucination_fuse.audit.record') as record:
                @Fuse().guard(str(path))
                def publish(payload):
                    return 'ok'
                publish('The survey included 100 participants.')
                events = [call.args[0] for call in record.call_args_list]
                self.assertEqual(events, ['decision', 'execution_started', 'execution_completed'])
                hashes = [call.args[1]['report_sha256'] for call in record.call_args_list]
                self.assertEqual(len(set(hashes)), 1)

    def test_cli_human_rewrite_runs_and_is_audited(self):
        from hallucination_fuse.__main__ import main
        with patch('hallucination_fuse.__main__.rewrite', return_value={'text': 'Proposed only', 'engine': 'test'}) as rewrite:
            with patch('hallucination_fuse.audit.record', return_value='0' * 64) as record:
                with patch('hallucination_fuse.audit.verify', return_value=(True, 3, None)):
                    with redirect_stdout(io.StringIO()):
                        main([str(ROOT / 'tests/fixtures/synthetic_wfh.txt'), '--no-network', '--human', 'rewrite'])
                rewrite.assert_called_once()
                self.assertIn('rewrite', [c.args[0] for c in record.call_args_list])


class AuditTests(unittest.TestCase):
    def test_edits_and_tail_deletion(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'audit.jsonl'
            audit.record('one', {}, path)
            head = audit.record('two', {}, path)
            self.assertTrue(audit.verify(path, expected_head=head)[0])
            lines = path.read_text().splitlines(True)
            path.write_text(lines[0])
            self.assertFalse(audit.verify(path, expected_head=head)[0])
            path.write_text(lines[0].replace('one', 'tampered'))
            self.assertFalse(audit.verify(path)[0])
            with self.assertRaises(ValueError):
                audit.record('three', {}, path)

    def test_concurrent_writers_preserve_chain(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'audit.jsonl'
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda n: audit.record('test', {'n': n}, path), range(20)))
            self.assertEqual(audit.verify(path), (True, 20, None))
