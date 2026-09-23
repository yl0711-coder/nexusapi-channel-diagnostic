#!/usr/bin/env python3
"""报告页的受保护运行控制 API；不返回凭据或请求正文。"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException
import uvicorn

import hourly_channel_diagnostic as diagnostic
import manage


ROOT = Path(__file__).resolve().parent


class ControlError(Exception):
    """可安全显示给控制台用户的控制错误。"""


class ProcessController:
    def __init__(self, state_dir: Path, credentials: Path):
        self.state_dir = state_dir.resolve()
        self.credentials = credentials.resolve()
        self.config_path = self.state_dir / "config.json"
        self.data_dir = self.state_dir / "data"
        self.report_path = self.state_dir / "reports/report.html"
        self.log_path = self.state_dir / "reports/worker.log"
        self.desired_path = self.state_dir / "control_state.json"
        self._process: subprocess.Popen[str] | None = None
        self._mode: str | None = None
        self._started_at: float | None = None
        self._last_exit_code: int | None = None
        self._last_stop_reason: str | None = None
        self._lock = threading.RLock()

    def _save_desired(self, running: bool) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.state_dir,
                                             prefix="control_state.", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"daemon_requested": running}, handle)
                handle.flush()
                os.fchmod(handle.fileno(), 0o600)
            temporary.replace(self.desired_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _desired_running(self) -> bool:
        try:
            value = json.loads(self.desired_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as exc:
            raise ControlError("控制状态文件无效，请检查私有状态目录") from exc
        if not isinstance(value, dict) or type(value.get("daemon_requested")) is not bool:
            raise ControlError("控制状态文件无效，请检查私有状态目录")
        return value["daemon_requested"]

    def _reconcile(self) -> None:
        db_path = self.data_dir / "diagnostic.sqlite3"
        try:
            diagnostic.recover_orphaned_runs(db_path)
            if db_path.exists():
                config = self._config()
                diagnostic.build_report(db_path, self.report_path, str(config["timezone"]),
                                        config["channels"], config["test_models"])
        except (RuntimeError, OSError, sqlite3.Error, ValueError) as exc:
            self._last_stop_reason = "recovery_failed"
            raise ControlError("中断记录修复失败，请检查运行锁、数据库和报告目录") from exc

    def _reap(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self._last_exit_code = self._process.returncode
            self._process = None
            self._started_at = None
            mode = self._mode
            self._mode = None
            if self._last_exit_code != 0 or mode == "daemon":
                self._last_stop_reason = "unexpected_exit"
            self._reconcile()

    def restore(self, auto_start: bool = True) -> None:
        with self._lock:
            self._reconcile()
            if not auto_start and self._desired_running():
                self._last_stop_reason = "control_token_missing"
            elif auto_start and self._desired_running():
                try:
                    self.start()
                except ControlError:
                    self._last_stop_reason = "resume_failed"

    def shutdown(self) -> None:
        with self._lock:
            self._stop_process(operator=False)

    def _config(self) -> dict[str, Any]:
        try:
            return diagnostic.load_config(self.config_path)
        except (OSError, ValueError, SystemExit, TypeError) as exc:
            raise ControlError("配置文件无效，无法启动") from exc

    def _validate_start(self, config: dict[str, Any]) -> None:
        enabled = [channel for channel in config.get("channels", []) if channel.get("enabled", True)]
        if not enabled:
            raise ControlError("没有启用渠道，请先点击“启用全部渠道”")
        try:
            credentials = diagnostic.load_credentials(self.credentials)
        except (OSError, ValueError) as exc:
            raise ControlError("凭据文件无效或权限不是 0600/0400") from exc
        missing = [channel.get("name", "unnamed") for channel in enabled
                   if not diagnostic.channel_credentials(channel, credentials)[0]]
        if missing:
            raise ControlError(f"有 {len(missing)} 个启用渠道缺少凭据")

    def enable_all(self) -> None:
        with self._lock:
            try:
                manage.configure_enabled(self.state_dir, [], True)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, SystemExit) as exc:
                raise ControlError("启用渠道失败，请检查状态目录权限和配置") from exc

    def _launch(self, command_name: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-B", str(ROOT / "hourly_channel_diagnostic.py"), command_name,
                   "--config", str(self.config_path), "--credentials", str(self.credentials),
                   "--data-dir", str(self.data_dir), "--output", str(self.report_path),
                   "--confirm-live"]
        try:
            log = self.log_path.open("a", encoding="utf-8")
            self._process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                             start_new_session=True, text=True)
            log.close()
        except OSError as exc:
            if "log" in locals():
                log.close()
            raise ControlError("无法启动检测进程") from exc
        self._started_at = time.time()
        self._last_exit_code = None
        self._mode = command_name
        self._last_stop_reason = None

    def start(self) -> None:
        with self._lock:
            self._reap()
            if self._process is not None:
                if self._mode == "daemon":
                    return
                raise ControlError("单轮检测正在执行，请等待或停止后再启动定时检测")
            config = self._config()
            self._validate_start(config)
            try:
                self._save_desired(True)
            except OSError as exc:
                raise ControlError("无法保存定时检测的启动状态，请检查状态目录权限") from exc
            try:
                self._launch("daemon")
            except ControlError:
                try:
                    self._save_desired(False)
                except OSError as exc:
                    raise ControlError("检测未启动，且无法清除自动恢复状态；请检查状态目录") from exc
                raise

    def run_once(self) -> None:
        with self._lock:
            self._reap()
            if self._process is not None:
                raise ControlError("检测进程正在运行，请先等待本轮完成")
            config = self._config()
            self._validate_start(config)
            self._launch("run-once")

    def _stop_process(self, operator: bool) -> None:
        self._reap()
        if self._process is None:
            return
        process = self._process
        mode = self._mode
        signalled = False
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGINT if operator else signal.SIGTERM)
                signalled = True
            except ProcessLookupError:
                pass
        forced = False
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=5)
        self._last_exit_code = process.returncode
        if forced:
            self._last_stop_reason = "forced_stop"
        elif not signalled:
            self._last_stop_reason = "unexpected_exit" if mode == "daemon" or process.returncode != 0 else "completed"
        else:
            self._last_stop_reason = "operator_stop" if operator else "system_shutdown"
        self._process = None
        self._mode = None
        self._started_at = None
        self._reconcile()

    def stop(self) -> None:
        with self._lock:
            persistence_error = None
            try:
                self._save_desired(False)
            except OSError as exc:
                persistence_error = exc
            self._stop_process(operator=True)
            if persistence_error is not None:
                raise ControlError("当前检测已停止，但停止意愿未能保存；请修复状态目录权限，避免容器重启后恢复") from persistence_error

    def _last_run(self) -> dict[str, Any] | None:
        db_path = self.data_dir / "diagnostic.sqlite3"
        if not db_path.exists():
            return None
        try:
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """SELECT r.id,r.started_at,r.finished_at,r.mock,r.status,r.error,r.planned_requests,
                       COUNT(o.id),SUM(CASE WHEN o.ok=1 THEN 1 ELSE 0 END)
                       FROM runs r LEFT JOIN observations o ON o.run_id=r.id
                       GROUP BY r.id ORDER BY r.id DESC LIMIT 1"""
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return {"id": row[0], "started_at": row[1], "finished_at": row[2],
                "mode": "mock" if row[3] else "live", "status": row[4],
                "label": diagnostic.run_status_label(row[4], row[5]),
                "reason": row[5], "planned_requests": row[6],
                "executed_requests": row[7], "succeeded_requests": row[8] or 0}

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._reap()
            config = self._config()
            channels = config.get("channels", [])
            state = "running" if self._process is not None else "stopped"
            if self._process is None and self._last_stop_reason in ("unexpected_exit", "forced_stop", "resume_failed", "recovery_failed", "control_token_missing"):
                state = "failed"
            last_run = self._last_run()
            phase = state
            if state == "running":
                recent_run = last_run and last_run["status"] == "running" and self._started_at is not None and \
                    diagnostic.parse_iso(last_run["started_at"]).timestamp() >= self._started_at - 1
                phase = "checking" if recent_run else "waiting" if self._mode == "daemon" else "starting"
            return {
                "state": state,
                "phase": phase,
                "mode": self._mode,
                "pid": self._process.pid if self._process is not None else None,
                "started_at": self._started_at,
                "last_exit_code": self._last_exit_code,
                "last_stop_reason": self._last_stop_reason,
                "total_channels": len(channels),
                "enabled_channels": sum(bool(channel.get("enabled", True)) for channel in channels),
                "test_models": config.get("test_models", list(diagnostic.DEFAULT_TEST_MODELS)),
                "requests_per_channel": (int(config["rounds"]) * 6 + int(config["juice_runs"]) * 5)
                * len(config["test_models"]),
                "last_run": last_run,
            }


def create_app(controller: ProcessController, token: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if isinstance(controller, ProcessController):
            controller.restore(auto_start=bool(token))
        try:
            yield
        finally:
            if isinstance(controller, ProcessController):
                controller.shutdown()

    app = FastAPI(title="小时渠道诊断控制服务", docs_url=None, redoc_url=None, lifespan=lifespan)

    def authorize(value: str | None) -> None:
        if not token:
            raise HTTPException(status_code=503, detail="控制服务未配置令牌")
        if not value or not hmac.compare_digest(value, token):
            raise HTTPException(status_code=403, detail="控制令牌无效")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        if not token:
            raise HTTPException(status_code=503, detail="控制服务未配置令牌")
        return {"status": "ok"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        try:
            return controller.status()
        except ControlError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/enable-all")
    def enable_all(x_control_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_control_token)
        try:
            controller.enable_all()
            return controller.status()
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/start")
    def start(x_control_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_control_token)
        try:
            controller.start()
            return controller.status()
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/run-once")
    def run_once(x_control_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_control_token)
        try:
            controller.run_once()
            return controller.status()
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/stop")
    def stop(x_control_token: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(x_control_token)
        try:
            controller.stop()
            return controller.status()
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8098, type=int)
    args = parser.parse_args()
    app = create_app(ProcessController(args.state_dir, args.credentials), os.environ.get("DIAGNOSTIC_CONTROL_TOKEN", ""))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
