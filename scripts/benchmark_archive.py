"""Matched synthetic archive benchmark; never touches a real research archive."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from playground.archive import Archive


def benchmark(trials, repeats):
    with tempfile.TemporaryDirectory(prefix='playground-archive-benchmark-') as directory:
        archive = Archive(Path(directory) / 'archive')
        try:
            started = time.perf_counter()
            with archive.db:
                archive.db.executemany('INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ((str(i), 'synthetic', 'simulation', 'explore', i, i+1,
                      '{}', '{}', '{}', '{}', (i % 100) / 100, 'completed', None) for i in range(trials)))
            population_seconds = time.perf_counter() - started
            def reference():
                counts = {table: archive.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                          for table in ('runs','generations','experiments','findings','evaluations','artifacts')}
                values = archive.db.execute("SELECT AVG(error),MIN(error),MAX(error) FROM experiments WHERE status='completed'").fetchone()
                return counts, dict(zip(('mean','min','max'),values))
            baseline, cached = [], []
            for _ in range(repeats):
                started = time.perf_counter(); counts, errors = reference(); baseline.append(time.perf_counter()-started)
                started = time.perf_counter(); result = archive.summary(); cached.append(time.perf_counter()-started)
                if any(result[key] != value for key,value in counts.items()):
                    raise RuntimeError('counter parity failure')
                if any(abs(result['prediction_error'][key]-value) > 1e-9 for key,value in errors.items()):
                    raise RuntimeError('error summary parity failure')
            def timing(values):
                values = sorted(values)
                return {'median_ms': statistics.median(values)*1000, 'p95_ms': values[min(len(values)-1,int(len(values)*.95))]*1000}
            return {'trials':trials,'repeats':repeats,'population_seconds':population_seconds,
                    'reference':timing(baseline),'cached':timing(cached),'parity_passed':True,
                    'sqlite_integrity':archive.db.execute('PRAGMA integrity_check').fetchone()[0]}
        finally:
            archive.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trials',type=int,default=100000)
    parser.add_argument('--repeats',type=int,default=20)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not 1 <= args.trials <= 500000 or not 1 <= args.repeats <= 100:
        parser.error('trials must be 1..500000; repeats 1..100')
    # Never silently replace an earlier evidence report.
    with args.report.open('x',encoding='utf-8') as output:
        report={'schema_version':1,'kind':'synthetic_archive_benchmark',
                'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'platform':platform.platform(),'python':platform.python_version(),
                'physical_hardware_verified':False,'spark_runtime_verified':False,
                'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (ROOT/'src/playground/archive.py',Path(__file__).resolve())}}
        report.update(benchmark(args.trials,args.repeats))
        json.dump(report,output,indent=2)
        output.write('\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
