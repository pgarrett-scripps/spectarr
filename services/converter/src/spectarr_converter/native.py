"""Explicit, capability-checked adapter for the local Rust conversion executable."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .recipes import Recipe
from .service import ProcessReport


class NativeMsconvertRunner:
    """Run one native conversion with preflight, process cleanup and structured provenance."""

    def __init__(self, binary: Path | None = None) -> None:
        configured = (
            str(binary) if binary else os.getenv("SPECTARR_MSCONVERT_RS_BINARY")
        )
        if not configured:
            raise ValueError(
                "Set SPECTARR_MSCONVERT_RS_BINARY to enable the explicit msconvert-rs backend"
            )
        self.binary = Path(configured).resolve(strict=True)
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise ValueError("Native converter must be an executable file")

    def run(
        self,
        source: Path,
        output_dir: Path,
        recipe: Recipe,
        image: str,
        source_name: str | None = None,
        cancel_event: threading.Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> ProcessReport:
        if recipe.converter != "msconvert-rs":
            raise ValueError("Native runner requires an explicit msconvert-rs recipe")
        if cancel_event and cancel_event.is_set():
            return ProcessReport(
                130, "", "Conversion cancelled before preflight", cancelled=True
            )
        binary_hash = self._sha256(self.binary)
        source_hash = self._sha256(source, cancel_event) if source.is_file() else None
        capability_process = self._execute(
            [str(self.binary), "capabilities", "--json"],
            cancel_event,
            None,
            deadline=15,
        )
        if capability_process.cancelled:
            return capability_process
        if capability_process.return_code:
            raise ValueError(
                f"Native capability check failed: {capability_process.stderr[-2000:]}"
            )
        capabilities = json.loads(capability_process.stdout)
        if capabilities.get("schema_version") != 1:
            raise ValueError("Unsupported native capability schema")
        if recipe.output_format.value.lower() not in {
            value.lower() for value in capabilities.get("outputs", [])
        }:
            raise ValueError(
                "Native backend does not advertise the requested output format"
            )
        version = str(capabilities["version"])
        if recipe.converter_version and recipe.converter_version != version:
            raise ValueError(
                "Recipe converter version does not match the native executable"
            )
        identity = f"msconvert-rs@sha256:{binary_hash}"
        scientific_name = source_name or source.name
        if (
            Path(scientific_name).name != scientific_name
            or scientific_name in {"", ".", ".."}
            or "\\" in scientific_name
        ):
            raise ValueError("Source name must be a plain filename")
        with tempfile.TemporaryDirectory(
            prefix="native-input-", dir=output_dir.parent
        ) as staging:
            native_source = source
            if source.is_file() and source.name != scientific_name:
                native_source = Path(staging) / scientific_name
                try:
                    os.link(source, native_source)
                except OSError:
                    shutil.copyfile(source, native_source)
            command = [
                str(self.binary),
                str(native_source),
                *recipe.arguments,
                "-o",
                str(output_dir),
                "--json",
                "--progress-jsonl",
            ]
            preflight = self._execute(
                [*command, "--dry-run"], cancel_event, progress, deadline=60
            )
            if preflight.cancelled or preflight.return_code:
                return ProcessReport(
                    preflight.return_code,
                    preflight.stdout,
                    preflight.stderr,
                    tuple(command),
                    version,
                    preflight.cancelled,
                    identity,
                )
            preflight_payload = json.loads(preflight.stdout)
            if not isinstance(preflight_payload, dict) or preflight_payload.get("status") != "planned":
                raise ValueError("Native preflight did not report a conversion plan")
            result = self._execute(command, cancel_event, progress)
            if result.cancelled or result.return_code:
                return ProcessReport(
                    result.return_code,
                    result.stdout,
                    result.stderr,
                    tuple(command),
                    version,
                    result.cancelled,
                    identity,
                )
            payload = json.loads(result.stdout)
            results = payload.get("results", [])
            if len(results) != 1 or results[0].get("status") != "success":
                raise ValueError(
                    "Native converter did not report exactly one successful result"
                )
            report = results[0]["report"]
            reported_output = Path(report["output"])
            output = reported_output.resolve(strict=True)
            if (
                not output.is_relative_to(output_dir.resolve())
                or reported_output.is_symlink()
            ):
                raise ValueError("Native report output escaped the job directory")
            if self._sha256(output) != report.get("output_sha256"):
                raise ValueError("Native output does not match its conversion report")
            if source_hash is not None and (
                self._sha256(source, cancel_event) != source_hash
                or report.get("input_sha256") != source_hash
            ):
                raise ValueError("Source changed during native conversion")
            if self._sha256(self.binary) != binary_hash:
                raise ValueError("Native executable changed during conversion")
            provenance = {
                "capabilities": capabilities,
                "preflight": preflight_payload,
                "conversion": report,
                "binary_sha256": binary_hash,
                "source_path": str(source),
                "source_name": scientific_name,
            }
            return ProcessReport(
                0,
                result.stdout,
                result.stderr,
                tuple(command),
                version,
                False,
                identity,
                provenance,
            )

    @staticmethod
    def _sha256(path: Path, cancel_event: threading.Event | None = None) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError(
                        "Conversion cancelled during checksum verification"
                    )
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _execute(
        command: list[str],
        cancel_event: threading.Event | None,
        progress: Callable[[str, float], None] | None,
        deadline: float = 0,
    ) -> ProcessReport:
        started = time.monotonic()
        kwargs = (
            {"start_new_session": True}
            if os.name == "posix"
            else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        tail: list[str] = []
        errors: list[Exception] = []
        content = bytearray()
        report_limit = 4 * 1024 * 1024
        line_limit = 65536

        def drain_stdout() -> None:
            try:
                assert process.stdout is not None
                overflow = False
                while chunk := process.stdout.read1(65536):
                    if len(content) + len(chunk) > report_limit:
                        if not overflow:
                            errors.append(
                                ValueError("Native report exceeded the size limit")
                            )
                            overflow = True
                    elif not overflow:
                        content.extend(chunk)
            except OSError as error:
                errors.append(error)

        def handle_line(raw: bytes) -> None:
            line = raw.decode("utf-8", errors="replace")
            tail.append(line)
            del tail[:-64]
            if not progress:
                return
            try:
                message = json.loads(line)
            except ValueError:
                return
            event = (
                message.get("event", {}) if message.get("kind") == "progress" else {}
            )
            stage = event.get("stage")
            if stage:
                base, width = {
                    "started": (0.0, 0.0),
                    "hashing": (0.02, 0.0),
                    "validating": (0.05, 0.35),
                    "writing": (0.4, 0.4),
                    "verifying": (0.8, 0.0),
                    "completed": (0.85, 0.0),
                }.get(stage, (0.0, 0.0))
                fraction = min(
                    1.0,
                    event.get("spectra_processed", 0)
                    / max(1, event.get("spectra_total") or 1),
                )
                progress(stage, base + width * fraction)

        def drain_stderr() -> None:
            try:
                assert process.stderr is not None
                pending = bytearray()
                overflow = False
                while chunk := process.stderr.read1(4096):
                    if overflow:
                        continue
                    pending.extend(chunk)
                    while (end := pending.find(b"\n")) >= 0:
                        if end + 1 > line_limit:
                            errors.append(
                                ValueError("Native stderr line exceeded the size limit")
                            )
                            overflow = True
                            pending.clear()
                            break
                        line = bytes(pending[: end + 1])
                        del pending[: end + 1]
                        handle_line(line)
                    if len(pending) > line_limit:
                        errors.append(
                            ValueError("Native stderr line exceeded the size limit")
                        )
                        overflow = True
                        pending.clear()
                if pending and not overflow:
                    handle_line(bytes(pending))
            except Exception as error:  # noqa: BLE001 - forward callback failures to the supervising thread
                errors.append(error)

        threads = [
            threading.Thread(target=drain_stdout, daemon=True),
            threading.Thread(target=drain_stderr, daemon=True),
        ]
        for thread in threads:
            thread.start()
        cancelled = False
        timed_out = False
        try:
            while process.poll() is None:
                cancelled = bool(cancel_event and cancel_event.is_set())
                timed_out = bool(deadline and time.monotonic() - started > deadline)
                if cancelled or timed_out or errors:
                    NativeMsconvertRunner._stop(process)
                    if timed_out:
                        tail.append("Native preflight timed out")
                    break
                time.sleep(0.05)
            process.wait()
        finally:
            if process.poll() is None:
                NativeMsconvertRunner._stop(process)
            for thread in threads:
                thread.join(timeout=5)
            for pipe, thread in zip(
                (process.stdout, process.stderr), threads, strict=True
            ):
                if thread.is_alive():
                    errors.append(
                        RuntimeError("Native child retained a process output pipe")
                    )
                elif pipe:
                    pipe.close()
        if errors:
            raise RuntimeError(f"Native process supervision failed: {errors[0]}")
        return ProcessReport(
            124 if timed_out else process.returncode,
            content.decode("utf-8"),
            "".join(tail),
            tuple(command),
            cancelled=cancelled,
        )

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGINT)
            else:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
        except ProcessLookupError:
            process.wait()
