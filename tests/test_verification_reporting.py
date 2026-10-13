"""The acceptance reporter cannot overwrite evidence or hide skips/source drift."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_reporter', ROOT / 'scripts/verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerificationReportingTests(unittest.TestCase):
    def test_report_cannot_replace_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text('original evidence')
            with self.assertRaises(FileExistsError):
                verify.save_report(path, {'passed': True})
            self.assertEqual(path.read_text(), 'original evidence')

    def test_skips_and_source_changes_prevent_aggregate_pass(self):
        summary = json.dumps({'status':'completed','generations':4,'experiments':112,'evaluations':4,'findings':1})
        for skipped, changed, expected in ((0,False,0),(1,False,1),(0,True,1)):
            with self.subTest(skipped=skipped, changed=changed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'report.json'
                tests = SimpleNamespace(returncode=0, stdout='', stderr=f'Ran 2 tests\nOK (skipped={skipped})\n')
                demo = SimpleNamespace(returncode=0, stdout=summary, stderr='')
                hashes = [{'src/example.py':'old'}, {'src/example.py':'new' if changed else 'old'}]
                with patch.object(verify.sys,'argv',['verify.py','--report',str(path)]), \
                     patch.object(verify,'source_hashes',side_effect=hashes), \
                     patch.object(verify.platform,'platform',return_value='test-platform'), \
                     patch.object(verify.subprocess,'run',side_effect=[tests,demo]), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = verify.main()
                self.assertEqual(code, expected)
                report = json.loads(path.read_text())
                self.assertEqual(report['passed'], expected == 0)
                self.assertFalse(report['physical_hardware_verified'])
                self.assertFalse(report['spark_runtime_verified'])


if __name__ == '__main__':
    unittest.main()
