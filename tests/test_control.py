"""控制 API 与本机 Mock 子进程的状态回归；不访问真实渠道。"""
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


control_server = importlib.import_module("control_server")
ROOT = Path(__file__).resolve().parents[1]


class FakeController:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"state": "stopped", "enabled_channels": 0, "total_channels": 2,
                "test_models": ["gpt-5.6-sol", "gpt-6-astra"], "last_run": None}

    def enable_all(self):
        self.calls.append("enable_all")

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def run_once(self):
        self.calls.append("run_once")


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.client = TestClient(control_server.create_app(self.controller, "synthetic-token"))

    def test_status_is_readable_without_token_and_has_no_secret(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["test_models"], ["gpt-5.6-sol", "gpt-6-astra"])
        self.assertNotIn("token", response.text.lower())

    def test_missing_server_token_is_not_healthy_or_writable(self):
        client = TestClient(control_server.create_app(self.controller, ""))
        self.assertEqual(client.get("/healthz").status_code, 503)
        self.assertEqual(client.post("/api/start", headers={"X-Control-Token": "synthetic-token"}).status_code, 503)

    def test_mutations_require_control_token(self):
        self.assertEqual(self.client.post("/api/enable-all").status_code, 403)
        self.assertEqual(self.client.post("/api/enable-all", headers={"X-Control-Token": "wrong"}).status_code, 403)
        response = self.client.post("/api/enable-all", headers={"X-Control-Token": "synthetic-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.controller.calls, ["enable_all"])

    def test_start_and_stop_are_forwarded_after_authorization(self):
        headers = {"X-Control-Token": "synthetic-token"}
        self.assertEqual(self.client.post("/api/start", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/stop", headers=headers).status_code, 200)
        self.assertEqual(self.controller.calls, ["start", "stop"])

    def test_run_once_is_forwarded_after_authorization(self):
        response = self.client.post("/api/run-once", headers={"X-Control-Token": "synthetic-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.controller.calls, ["run_once"])


class ProcessControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="diagnostic-control-", dir=os.environ["DIAGNOSTIC_TEST_ROOT"])
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        (self.state / "reports").mkdir()
        config = json.loads((ROOT / "config.example.json").read_text())
        config.update(rounds=1, juice_runs=1, data_dir="data")
        (self.state / "config.json").write_text(json.dumps(config))
        self.controller = control_server.ProcessController(self.state, self.state / "credentials.json")

    def test_manual_stop_preserves_real_exit_and_interrupted_report(self):
        script = """import sys, time
import hourly_channel_diagnostic as diagnostic
original = diagnostic.mock_call
def slow(surface, effort, package):
    time.sleep(.08)
    return original(surface, effort, package)
diagnostic.mock_call = slow
sys.argv = ['diagnostic', 'run-once', '--mock', '--config', sys.argv[1],
            '--data-dir', sys.argv[2], '--output', sys.argv[3]]
raise SystemExit(diagnostic.main())
"""
        db = self.state / "data/diagnostic.sqlite3"
        report = self.state / "reports/report.html"
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.state / "config.json"), str(db.parent), str(report)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.controller._process = process
        self.controller._mode = "run-once"
        self.controller._started_at = time.time()
        deadline = time.monotonic() + 5
        count = 0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"Mock 子进程过早退出：{process.returncode}")
            if db.exists():
                try:
                    with sqlite3.connect(db) as connection:
                        count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
                except sqlite3.Error:
                    pass
            if count:
                break
            time.sleep(.02)
        self.assertGreater(count, 0)
        self.controller.stop()
        status = self.controller.status()
        self.assertEqual(process.returncode, 130)
        self.assertEqual(status["last_exit_code"], 130)
        self.assertEqual(status["last_stop_reason"], "operator_stop")
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(status["last_run"]["label"], "管理员中断")
        self.assertLess(status["last_run"]["executed_requests"], status["last_run"]["planned_requests"])
        self.assertIn("管理员中断", report.read_text())

    def test_unexpected_child_exit_is_not_a_clean_stop(self):
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.controller._process = process
        self.controller._mode = "run-once"
        process.wait(timeout=5)
        status = self.controller.status()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["last_exit_code"], 7)
        self.assertEqual(status["last_stop_reason"], "unexpected_exit")

    def test_system_shutdown_keeps_daemon_intent_for_restart(self):
        self.controller._save_desired(True)
        process = subprocess.Popen(
            [sys.executable, "-B", str(ROOT / "hourly_channel_diagnostic.py"), "daemon", "--mock",
             "--config", str(self.state / "config.json"), "--data-dir", str(self.state / "data"),
             "--output", str(self.state / "reports/report.html")],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            start_new_session=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertIn("小时调度已启动", process.stdout.readline())
        self.controller._process = process
        self.controller._mode = "daemon"
        self.controller._started_at = time.time()
        self.assertEqual(self.controller.status()["phase"], "waiting")
        self.controller.shutdown()
        self.assertEqual(process.returncode, 143)
        self.assertEqual(self.controller.status()["last_stop_reason"], "system_shutdown")
        self.assertTrue(self.controller._desired_running())

    def test_explicit_daemon_intent_survives_controller_restart(self):
        self.controller._save_desired(True)
        restored = control_server.ProcessController(self.state, self.state / "credentials.json")
        with patch.object(restored, "_validate_start"), patch.object(restored, "_launch") as launch:
            restored.restore()
        launch.assert_called_once_with("daemon")
        self.assertTrue(restored._desired_running())

    def test_missing_control_token_does_not_silently_resume_daemon(self):
        self.controller._save_desired(True)
        restored = control_server.ProcessController(self.state, self.state / "credentials.json")
        with patch.object(restored, "_launch") as launch:
            restored.restore(auto_start=False)
        launch.assert_not_called()
        self.assertEqual(restored.status()["state"], "failed")
        self.assertEqual(restored.status()["last_stop_reason"], "control_token_missing")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([*(ControlTests(name) for name in ControlTests.__dict__ if name.startswith("test_")),
                               *(ProcessControlTests(name) for name in ProcessControlTests.__dict__ if name.startswith("test_"))])
