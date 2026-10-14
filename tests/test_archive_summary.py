"""Cached summaries must remain derived from committed raw evidence."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from playground.archive import Archive


class ArchiveSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name) / 'archive'
        self.archive = Archive(self.directory)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.archive.close())

    def check_summary(self):
        db = self.archive.db
        result = self.archive.summary()
        for table in ('runs', 'generations', 'experiments', 'findings', 'evaluations', 'artifacts'):
            self.assertEqual(result[table], db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
        expected = db.execute("SELECT AVG(error),MIN(error),MAX(error) FROM experiments WHERE status='completed'").fetchone()
        for name, value in zip(('mean', 'min', 'max'), expected):
            if value is None:
                self.assertIsNone(result['prediction_error'][name])
            else:
                self.assertAlmostEqual(result['prediction_error'][name], value)
        return result

    def trial(self, index, status='completed', error=0.25):
        self.archive.db.execute('INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (str(index), 'generation', 'simulation', 'explore', index, index+1,
             '{}', '{}', '{}', '{}', error, status, None))

    def test_empty_and_all_table_counts(self):
        self.check_summary()
        run = self.archive.start_run('simulation', {})
        generation = self.archive.start_generation(run)
        self.trial(1)
        self.archive.db.commit()
        self.archive.submit_finding(generation, 'fixture', '', ['1'])
        self.archive.add_evaluation(generation, {}, {})
        self.archive.record_artifact(generation, 'file', '0'*64, 0)
        self.check_summary()

    def test_updates_deletes_and_null_errors_match_raw(self):
        for i, value in enumerate((0.1, 0.2, 0.3, None)):
            self.trial(i, error=value)
        self.trial(5, 'pending', 100)
        self.archive.db.commit()
        self.check_summary()
        self.archive.db.execute("UPDATE experiments SET status='completed',error=0.4 WHERE id='5'")
        self.check_summary()
        self.archive.db.execute("UPDATE experiments SET status='interrupted' WHERE id='1'")
        self.archive.db.execute("DELETE FROM experiments WHERE id='2'")
        self.check_summary()
        self.archive.db.execute('DELETE FROM experiments')
        self.check_summary()

    def test_rollback_never_changes_summary(self):
        original = self.check_summary()
        try:
            with self.archive.db:
                self.trial(1)
                self.assertEqual(self.archive.summary()['experiments'], 1)
                raise RuntimeError('injected interruption')
        except RuntimeError:
            pass
        self.assertEqual(self.check_summary(), original)

    def test_legacy_migration_preserves_rows_and_restarts(self):
        self.trial(1)
        self.trial(2, 'pending', None)
        self.archive.db.commit()
        for row in self.archive.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'summary_%'").fetchall():
            self.archive.db.execute(f'DROP TRIGGER {row[0]}')
        self.archive.db.execute('DROP TABLE summary_counters')
        self.archive.db.commit()
        self.archive.close()
        self.archive = Archive(self.directory)
        self.check_summary()
        self.archive.recover_interrupted()
        self.check_summary()
        self.archive.close()
        self.archive = Archive(self.directory)
        self.check_summary()

    def test_viewer_reads_new_and_legacy_without_migrating(self):
        from playground.viewer import read_status
        self.trial(1)
        self.archive.db.commit()
        self.assertEqual(read_status(self.directory.parent)['experiments'], 1)
        for row in self.archive.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'summary_%'").fetchall():
            self.archive.db.execute(f'DROP TRIGGER {row[0]}')
        self.archive.db.execute('DROP TABLE summary_counters')
        self.archive.db.commit()
        self.assertEqual(read_status(self.directory.parent)['experiments'], 1)
        self.assertIsNone(self.archive.db.execute("SELECT 1 FROM sqlite_master WHERE name='summary_counters'").fetchone())

    def test_summary_work_is_bounded_at_large_trial_count(self):
        with self.archive.db:
            for i in range(20000):
                self.trial(i, error=(i % 100) / 100)
        calls = [0]
        def progress():
            calls[0] += 1
            return 0
        self.archive.db.set_progress_handler(progress, 100)
        result = self.archive.summary()
        self.archive.db.set_progress_handler(None, 0)
        self.assertEqual(result['experiments'], 20000)
        self.assertLess(calls[0], 100, 'summary must not rescan all experiments')
        self.check_summary()


if __name__ == '__main__':
    unittest.main()
