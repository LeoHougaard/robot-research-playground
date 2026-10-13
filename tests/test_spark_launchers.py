"""Linux launcher integration with real local processes and a fake Docker CLI.

No host Docker, model, Spark account, credentials, or physical hardware is used.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = (sys.platform.startswith("linux") and all(shutil.which(x) for x in ("bash", "flock", "timeout"))
             and hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"))


@unittest.skipUnless(SUPPORTED, "Linux /proc, pidfds, bash, flock and timeout required")
class SparkLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="spark-launcher-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.runtime = self.state / "runtime"
        self.runtime.mkdir(parents=True)
        (self.root / "scripts").mkdir()
        for script in (ROOT / "scripts").glob("spark-*.sh"):
            shutil.copy2(script, self.root / "scripts" / script.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.docker_state = self.root / "docker.json"
        self.docker_state.write_text("{}")
        self.docker_log = self.root / "docker.log"
        self.processes = []
        self.addCleanup(self.cleanup_processes)
        self.executable("id", "#!/bin/sh\necho leo\n")
        self.executable("uname", "#!/bin/sh\necho aarch64\n")
        # The only admission replaced by the fixture is disk/host identity.
        # All lifecycle/PID helpers execute unchanged with the real interpreter.
        self.executable("python3", f'''#!{sys.executable}
import os,pathlib,sys,time
if len(sys.argv)==6 and sys.argv[1]=='-' and sys.argv[2]==os.environ['FIXTURE_ROOT'] and not os.environ.get('FIXTURE_REAL_DISK'):
    sys.stdin.read()
    raise SystemExit(0)
if len(sys.argv)>3 and sys.argv[1]=='-c' and sys.argv[3]=='launch' and os.environ.get('FIXTURE_DELAY_REGISTRATION'):
    with (pathlib.Path(os.environ['FIXTURE_ROOT'])/'bootstrap-pids').open('a') as out: out.write(str(os.getpid())+'\\n')
    time.sleep(2)
os.execv(sys.executable,[sys.executable,*sys.argv[1:]])
''')
        self.executable("docker", f'''#!{sys.executable}
import json,os,pathlib,sys
args=sys.argv[1:]
if args[:1]==['--host']: args=args[2:]
path=pathlib.Path(os.environ['FIXTURE_DOCKER_STATE'])
data=json.loads(path.read_text())
with open(os.environ['FIXTURE_DOCKER_LOG'],'a') as out: out.write(json.dumps(args)+'\\n')
if args[:2]==['context','inspect']: print(json.dumps('unix:///fixture/docker.sock'))
elif args[0]=='info': print(json.dumps(dict(id=os.environ.get('FIXTURE_DAEMON_ID','fixture-daemon'),root=os.environ['FIXTURE_ROOT'])))
elif args[0]=='version' or args[:2]==['image','inspect']: print('fixture')
elif args[0]=='ps':
    if os.environ.get('FIXTURE_LIST_FAIL'): raise SystemExit(17)
    session=next(a.split('=',2)[2] for a in args if a.startswith('label='))
    ids=[key for key,value in data.items() if value==session]
    if os.environ.get('FIXTURE_EXTRA_ID'): ids.append(os.environ['FIXTURE_EXTRA_ID'])
    print('\\n'.join(ids))
elif args[0]=='inspect': print(data[args[-1]])
elif args[:2]==['rm','--force']:
    if os.environ.get('FIXTURE_RM_FAIL'): raise SystemExit(19)
    if not os.environ.get('FIXTURE_KEEP_CONTAINER'):
        for key in args[2:]: del data[key]
        path.write_text(json.dumps(data))
else: raise SystemExit('Unexpected Docker fixture call: '+repr(args))
''')
        pkg = self.root / "src" / "playground"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import os\nif os.environ.get('FIXTURE_REAL_PACKAGE'): __path__.append(os.environ['FIXTURE_REAL_PACKAGE'])\n")
        (pkg / "llm.py").write_text("class LocalModel:\n def __init__(self,*a,**kw): pass\n def discover(self): return []\n")
        (pkg / "__main__.py").write_text('''import http.server,os,pathlib,sys,time
role=sys.argv[1]
root=pathlib.Path(os.environ['FIXTURE_ROOT'])
if role in ('run','demo','serve'):
    with (root/'processes').open('a') as out: out.write(str(os.getpid())+'\\n')
if role in ('run','demo'):
    print('worker fixture',flush=True)
    if os.environ.get('FIXTURE_REAL_PACKAGE'):
        from playground.research import ResearchSupervisor,simulation_controller
        controller,clock=simulation_controller()
        state=sys.argv[sys.argv.index('--state-dir')+1]
        supervisor=ResearchSupervisor(state,controller,clock=clock,config={'disk_reserve_bytes':1})
        try: (root/'provenance-bound').write_text('ok')
        finally: supervisor.close()
    if os.environ.get('FIXTURE_FLOOD'):
        sys.stdout.write('x'*(10*1024*1024)); sys.stdout.flush()
    behavior=os.environ.get('FIXTURE_WORKER','sleep')
    if behavior=='success': raise SystemExit(0)
    if behavior=='failure': raise SystemExit(23)
    if behavior=='delayed_failure': time.sleep(.5); raise SystemExit(24)
    time.sleep(90)
elif role=='serve':
    time.sleep(float(os.environ.get('FIXTURE_SERVER_DELAY','0')))
    port=int(sys.argv[sys.argv.index('--port')+1])
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        def log_message(self,*args): pass
    http.server.HTTPServer(('127.0.0.1',port),Handler).serve_forever()
''')
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", FIXTURE_ROOT=str(self.root),
                        FIXTURE_DOCKER_STATE=str(self.docker_state), FIXTURE_DOCKER_LOG=str(self.docker_log),
                        PLAYGROUND_STATE_DIR=str(self.state), PLAYGROUND_PORT=str(port))
        self.env.pop("PLAYGROUND_SESSION_ID", None)
        self.env.pop("DOCKER_HOST", None)
        self.env.pop("DOCKER_CONTEXT", None)

    def executable(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def cleanup_processes(self):
        # Only fixture children started by this test are registered here. Open
        # pidfds before killing so PID reuse cannot harm another test/workload.
        path = self.root / "processes"
        pids = [int(p) for p in path.read_text().split()] if path.exists() else []
        bootstrap = self.root / "bootstrap-pids"
        if bootstrap.exists(): pids.extend(int(p) for p in bootstrap.read_text().split())
        pids.extend(p.pid for p in self.processes if p.poll() is None)
        for pid in set(pids):
            try:
                fd = os.pidfd_open(pid)
                try:
                    args = Path(f"/proc/{pid}/cmdline").read_bytes()
                    env = Path(f"/proc/{pid}/environ").read_bytes()
                    if str(self.root).encode() in args or f"FIXTURE_ROOT={self.root}".encode() in env:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                finally:
                    os.close(fd)
            except (ProcessLookupError, FileNotFoundError):
                pass
        for process in self.processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    def run_script(self, name, *args, **env):
        return subprocess.run(["bash", str(self.root / "scripts" / f"spark-{name}.sh"), *args],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=20)

    def stale(self):
        old = "a" * 32
        (self.runtime / "session").write_text(json.dumps({"version": 2, "id": old, "mode": "research", "docker": {"endpoint": "unix:///fixture/docker.sock", "daemon_id": "fixture-daemon", "root": str(self.root)}}))
        self.docker_state.write_text(json.dumps({"1" * 64: old, "2" * 64: "b" * 32}))
        return old

    def docker_calls(self):
        return [json.loads(line) for line in self.docker_log.read_text().splitlines()]

    def assert_gone(self, pid):
        path = Path(f"/proc/{pid}/stat")
        self.assertTrue(not path.exists() or path.read_text().rsplit(") ", 1)[1].split()[0] in ("Z", "X"))

    def test_stale_session_reconciled_before_new_session_and_unrelated_preserved(self):
        old = self.stale()
        (self.runtime / "worker.log").write_text("old evidence\n")
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        new = json.loads((self.runtime / "session").read_text())["id"]
        self.assertNotEqual(new, old)
        self.assertEqual(json.loads(self.docker_state.read_text()), {"2" * 64: "b" * 32})
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [["rm", "--force", "1" * 64]])
        self.assertEqual([p.read_text() for p in (self.runtime / "logs").iterdir()], ["old evidence\n"])
        stopped = self.run_script("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse((self.runtime / "session").exists())

    def test_cleanup_failure_preserves_original_session_and_refuses_worker(self):
        for failure in ("FIXTURE_RM_FAIL", "FIXTURE_LIST_FAIL", "FIXTURE_KEEP_CONTAINER"):
            with self.subTest(failure=failure):
                old = self.stale()
                result = self.run_script("start", **{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads((self.runtime / "session").read_text())["id"], old)
                self.assertFalse((self.root / "processes").exists())

    def test_wrong_label_returned_by_listing_is_never_removed(self):
        old = self.stale()
        result = self.run_script("start", FIXTURE_EXTRA_ID="2" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.runtime / "session").read_text())["id"], old)
        self.assertNotIn(["rm", "--force", "2" * 64], self.docker_calls())

    def test_invalid_session_refuses_cleanup_without_docker(self):
        (self.runtime / "session").write_text("invalid-session\n")
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.docker_log.exists())
        self.assertTrue((self.runtime / "session").exists())

    def test_worker_early_and_delayed_failures_roll_back_viewer(self):
        for behavior in ("failure", "delayed_failure"):
            with self.subTest(behavior=behavior):
                result = self.run_script("start", FIXTURE_WORKER=behavior)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("Status UI:", result.stdout)
                self.assertFalse((self.runtime / "session").exists(), result.stderr)
                for pid in (self.root / "processes").read_text().split():
                    self.assert_gone(int(pid))

    def test_completed_demo_is_successful_and_needs_no_docker_cleanup(self):
        result = self.run_script("start", "--demo", FIXTURE_WORKER="success")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Bounded demo completed successfully", result.stdout)
        self.assertEqual(self.run_script("stop").returncode, 0)
        self.assertFalse(self.docker_log.exists())

    def test_failed_demo_is_not_treated_as_successful_completion(self):
        result = self.run_script("start", "--demo", FIXTURE_WORKER="failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.runtime / "session").exists(), result.stderr)

    def test_duplicate_start_retains_session_and_live_workers(self):
        first = self.run_script("start")
        self.assertEqual(first.returncode, 0, first.stderr)
        session = (self.runtime / "session").read_bytes()
        second = self.run_script("start")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already running", second.stdout)
        self.assertEqual((self.runtime / "session").read_bytes(), session)
        self.assertEqual(self.run_script("stop").returncode, 0)

    def test_reused_pid_or_changed_identity_is_never_signaled(self):
        for field, replacement in (("start_ticks", "0"), ("session_id", "f" * 32), ("state_dir", "/other")):
            with self.subTest(field=field):
                result = self.run_script("start")
                self.assertEqual(result.returncode, 0, result.stderr)
                path = self.runtime / "worker.pid"
                original = path.read_text()
                record = json.loads(original)
                record[field] = replacement
                path.write_text(json.dumps(record))
                stopped = self.run_script("stop")
                self.assertNotEqual(stopped.returncode, 0)
                self.assertTrue(Path(f"/proc/{record['pid']}").exists())
                self.assertTrue((self.runtime / "session").exists())
                path.write_text(original)
                self.assertEqual(self.run_script("stop").returncode, 0)

    def test_stale_boot_record_does_not_signal_live_unrelated_pid(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], env=self.env)
        self.processes.append(sleeper)
        old = self.stale()
        (self.runtime / "worker.pid").write_text(json.dumps({"version": 1, "pid": sleeper.pid, "boot_id": "old-boot"}))
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(sleeper.poll())
        self.assertFalse((self.runtime / "session").exists())

    def test_live_legacy_pid_requires_review_and_is_never_signaled(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], env=self.env)
        self.processes.append(sleeper)
        self.stale()
        (self.runtime / "worker.pid").write_text(f"{sleeper.pid} 0\n")
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(sleeper.poll())
        self.assertTrue((self.runtime / "session").exists())

    def test_launch_and_stop_are_serialized_without_child_retaining_lock(self):
        process = subprocess.Popen(["bash", str(self.root / "scripts" / "spark-start.sh")],
                                   env=dict(self.env, FIXTURE_SERVER_DELAY="1"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        deadline = time.monotonic() + 4
        while not (self.runtime / "session").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((self.runtime / "session").exists())
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operation is in progress", result.stderr)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr + stdout)
        self.assertEqual(self.run_script("stop").returncode, 0)

    def test_log_flood_is_capped_and_worker_survives(self):
        result = self.run_script("start", FIXTURE_FLOOD="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.runtime / "worker.log").stat().st_size, 8 * 1024 * 1024)
        self.assertEqual(self.run_script("stop").returncode, 0)

    def test_killed_launcher_retains_session_for_exact_stop(self):
        process = subprocess.Popen(["bash", str(self.root / "scripts" / "spark-start.sh")],
                                   env=dict(self.env, FIXTURE_SERVER_DELAY="4"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        deadline = time.monotonic() + 4
        while not (self.runtime / "server.pid").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((self.runtime / "server.pid").exists())
        session = (self.runtime / "session").read_bytes()
        process.kill()
        process.communicate(timeout=5)
        self.assertEqual((self.runtime / "session").read_bytes(), session)
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.runtime / "session").exists())
        for pid in (self.root / "processes").read_text().split():
            self.assert_gone(int(pid))

    def test_daemon_or_endpoint_change_preserves_session_without_cleanup(self):
        for overrides in ({"FIXTURE_DAEMON_ID": "other-daemon"}, {"DOCKER_HOST": "unix:///other/docker.sock"}, {"DOCKER_CONTEXT": "other"}):
            with self.subTest(overrides=overrides):
                old = self.stale()
                result = self.run_script("stop", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads((self.runtime / "session").read_text())["id"], old)
                self.assertEqual(len(json.loads(self.docker_state.read_text())), 2)
        self.assertFalse(any(c[0] == "rm" for c in self.docker_calls()))

    def test_legacy_session_without_daemon_binding_requires_operator_review(self):
        old = self.stale()
        (self.runtime / "session").write_text(old + "\n")
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker identity is missing", result.stderr)
        self.assertEqual((self.runtime / "session").read_text(), old + "\n")
        self.assertFalse(any(c[0] == "rm" for c in self.docker_calls()))

    def test_nonfinite_and_sub_gib_disk_floors_fail_before_work(self):
        for floor in ("nan", "inf", "-inf", "0.5"):
            with self.subTest(floor=floor):
                result = self.run_script("start", "--demo", FIXTURE_REAL_DISK="1", PLAYGROUND_MIN_FREE_DISK_GIB=floor)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("finite and at least 1 GiB", result.stderr)
                self.assertFalse((self.runtime / "session").exists())

    def test_delayed_child_cannot_launch_after_killed_parent_session_retired(self):
        process = subprocess.Popen(["bash", str(self.root / "scripts" / "spark-start.sh")],
                                   env=dict(self.env, FIXTURE_DELAY_REGISTRATION="1"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        deadline = time.monotonic() + 4
        while not (self.root / "bootstrap-pids").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.root / "bootstrap-pids").exists())
        process.kill()
        process.communicate(timeout=5)
        old = json.loads((self.runtime / "session").read_text())["id"]
        stopped = self.run_script("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        restarted = self.run_script("start")
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        new = json.loads((self.runtime / "session").read_text())["id"]
        self.assertNotEqual(old, new)
        time.sleep(.3)
        for role in ("worker", "server"):
            self.assertEqual(json.loads((self.runtime / f"{role}.pid").read_text())["session_id"], new)
        self.assertEqual(self.run_script("stop").returncode, 0)

    def test_fresh_launcher_metadata_allows_real_provenance_binding(self):
        result = self.run_script("start", FIXTURE_REAL_PACKAGE=str(ROOT / "src" / "playground"))
        self.assertEqual(result.returncode, 0, result.stderr + (self.runtime / "worker.log").read_text())
        self.assertEqual((self.root / "provenance-bound").read_text(), "ok")
        self.assertEqual(self.run_script("stop").returncode, 0)

    def test_inflight_crash_stays_blocked_even_after_exact_cleanup_is_empty(self):
        old = self.stale()
        marker = self.runtime / f"docker-inflight-{old}.json"
        marker.write_text(json.dumps({"version": 1, "session_id": old, "container_name": "playground-job-" + "d" * 32}))
        first = self.run_script("stop")
        self.assertNotEqual(first.returncode, 0)
        self.assertIn("Unresolved durable Docker intent", first.stderr)
        self.assertTrue((self.runtime / "session").exists())
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(self.docker_state.read_text()), {"2" * 64: "b" * 32})
        second = self.run_script("start")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("Unresolved durable Docker intent", second.stderr)
        self.assertEqual(json.loads((self.runtime / "session").read_text())["id"], old)
        self.assertFalse((self.root / "processes").exists())

    def test_orphan_intent_without_active_session_still_blocks_new_work(self):
        (self.runtime / ("docker-inflight-" + "a" * 32 + ".json")).write_text("interrupted write")
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unresolved durable Docker intent", result.stderr)
        self.assertFalse((self.root / "processes").exists())

    def test_stopped_session_identity_is_retained_durably(self):
        self.stale()
        source = (self.runtime / "session").read_bytes()
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.runtime / "sessions" / ("a" * 32 + ".json")).read_bytes(), source)
        self.assertFalse((self.runtime / "session").exists())
