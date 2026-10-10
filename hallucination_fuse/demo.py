"""Offline, non-destructive demonstration: python -m hallucination_fuse.demo."""
import json
from pathlib import Path

from . import Fuse, FuseTripped
from .parser import parse_report


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'output' / 'demo'
    output.mkdir(parents=True, exist_ok=True)
    for name in ('nuclear', 'wfh'):
        path = root / 'reports' / (name + '.pdf')
        report = parse_report(str(path))
        executed = []

        @Fuse('comms_publish').guard(str(path))
        def publish(payload):
            executed.append(payload)
            return 'Mock publication only'

        try:
            publish(report.analyzed_text)
        except FuseTripped as exc:
            d = exc.decision
            result = {'report': name, 'action': d.action,
                      'publication_executed': bool(executed),
                      'trust_range': [d.trust_low, d.trust_high],
                      'required': d.required, 'blast_radius': d.blast,
                      'reasons': [vars(r) for r in d.reasons],
                      'plan': d.plan, 'blind_spots': d.blind_spots,
                      'reliability': d.reliability, 'report_sha256': d.report_sha256}
            (output / (name + '.json')).write_text(json.dumps(result, indent=2) + '\n')
            print(f'{name}: {d.action} | publication executed: {bool(executed)}')
            print('Next: ' + '; '.join(d.plan))
        else:
            raise RuntimeError('Expected this demo report to hold execution; inspect the changed evidence/policy')


if __name__ == '__main__':
    main()
