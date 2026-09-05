"""Crash-safe adapter around the msconvert-cli package."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from xml.etree import ElementTree

from spxtacular import write_indexed_mzml_gzip

from .models import ConversionRequest, ConversionResult, OutputArtifact, OutputFormat
from .recipes import Recipe, compile_recipe, get_recipe


PINNED_DEFAULT_IMAGE = "proteowizard/pwiz-skyline-i-agree-to-the-vendor-licenses:3.0.26121-ed8dc8a"
SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class ProcessReport:
    """Captured converter process output."""

    return_code: int
    stdout: str
    stderr: str
    command: tuple[str, ...] = ()
    tool_version: str | None = None
    cancelled: bool = False


class ConversionRunner(Protocol):
    """Boundary used to replace Docker during tests."""

    def run(
        self,
        source: Path,
        output_dir: Path,
        recipe: Recipe,
        image: str,
        source_name: str | None = None,
        cancel_event: threading.Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> ProcessReport: ...


class MsconvertCliRunner:
    """Use msconvert-cli for command construction and execute one isolated job."""

    def __init__(
        self,
        container_data_root: Path | None = None,
        docker_data_root: Path | None = None,
        mount_map: dict[str, str] | None = None,
    ) -> None:
        configured_container = os.getenv("SPECTARR_CONTAINER_DATA_ROOT")
        configured_docker = os.getenv("SPECTARR_DOCKER_DATA_ROOT")
        self.container_data_root = (container_data_root or Path(configured_container)).resolve() if (
            container_data_root or configured_container
        ) else None
        self.docker_data_root = (docker_data_root or Path(configured_docker)).resolve() if (
            docker_data_root or configured_docker
        ) else None
        if (self.container_data_root is None) != (self.docker_data_root is None):
            raise ValueError("Both container and Docker data roots must be configured together")
        if mount_map is None and container_data_root is not None and docker_data_root is not None:
            mount_map = {str(self.container_data_root): str(self.docker_data_root)}
        self.mount_map = self._resolve_mount_map(mount_map)

    def _resolve_mount_map(self, mount_map: dict[str, str] | None) -> list[tuple[Path, Path]]:
        """Build (container path, host path) pairs, most specific mount first."""

        if mount_map is None:
            configured = os.getenv("SPECTARR_DOCKER_MOUNT_MAP")
            if configured:
                try:
                    mount_map = json.loads(configured)
                except json.JSONDecodeError as error:
                    raise ValueError("SPECTARR_DOCKER_MOUNT_MAP must be a JSON object of paths") from error
                if not isinstance(mount_map, dict):
                    raise ValueError("SPECTARR_DOCKER_MOUNT_MAP must be a JSON object of paths")
        if mount_map is None:
            if self.container_data_root is None or self.docker_data_root is None:
                return []
            mount_map = {str(self.container_data_root): str(self.docker_data_root)}
        pairs = [(Path(container), Path(host)) for container, host in mount_map.items()]
        return sorted(pairs, key=lambda pair: len(pair[0].parts), reverse=True)

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
        try:
            from msconvert_cli.converter import SimplePWizConverter
        except ImportError as error:
            raise RuntimeError(
                "msconvert-cli is not installed. Install the sibling package into this service environment."
            ) from error

        converter = SimplePWizConverter(docker_image=image)
        container_name = f"spectarr-msconvert-{uuid.uuid4().hex}"
        aliases = {source.resolve(): source_name} if source_name else None
        command = converter.build_docker_command(
            [source],
            output_dir,
            list(recipe.arguments),
            recipe.config_path,
            input_names=aliases,
            read_only_inputs=True,
            container_name=container_name,
        )
        command = self._map_docker_mount_sources(command)
        command = self._make_input_mounts_read_only(command)
        completed = converter.execute_command(
            command,
            output_dir,
            cancel_event,
            progress,
            container_name=container_name,
        )
        return ProcessReport(
            completed.return_code,
            completed.stdout,
            completed.stderr,
            completed.command,
            completed.tool_version,
            completed.cancelled,
        )

    def _map_docker_mount_sources(self, command: list[str]) -> list[str]:
        """Translate worker-container paths into paths visible to the host Docker daemon."""

        if not self.mount_map:
            return list(command)
        rewritten = list(command)
        for index, part in enumerate(rewritten[:-1]):
            if part != "-v":
                continue
            pieces = rewritten[index + 1].split(":")
            source = Path(pieces[0]).resolve()
            for container_root, host_root in self.mount_map:
                if source == container_root or source.is_relative_to(container_root):
                    pieces[0] = str(host_root / source.relative_to(container_root))
                    rewritten[index + 1] = ":".join(pieces)
                    break
        return rewritten

    @staticmethod
    def _alias_file_mount(command: list[str], source: Path, source_name: str) -> list[str]:
        """Give a content-addressed file its scientific filename inside Docker."""

        if Path(source_name).name != source_name or source_name in {"", ".", ".."}:
            raise ValueError("Source name must be a plain filename")
        rewritten = list(command)
        expected_mount = f"{source.parent}:/input"
        expected_argument = f"/input/{source.name}"
        for index, part in enumerate(rewritten):
            if part == expected_mount:
                rewritten[index] = f"{source}:/input/{source_name}"
            elif part == expected_argument:
                rewritten[index] = f"/input/{source_name}"
        return rewritten

    @staticmethod
    def _make_input_mounts_read_only(command: list[str]) -> list[str]:
        """Mark input and config mounts read-only while leaving output writable."""

        rewritten = list(command)
        for index, part in enumerate(rewritten[:-1]):
            if part != "-v":
                continue
            mount = rewritten[index + 1]
            container_path = mount.rsplit(":", 1)[-1]
            if container_path.startswith("/input") or container_path == "/config":
                rewritten[index + 1] = f"{mount}:ro"
        return rewritten


class ConversionService:
    """Validate, execute, discover, and verify one conversion job."""

    def __init__(
        self,
        scratch_root: Path,
        allowed_source_roots: tuple[Path, ...],
        image: str = PINNED_DEFAULT_IMAGE,
        runner: ConversionRunner | None = None,
    ) -> None:
        self.scratch_root = scratch_root.resolve()
        self.allowed_source_roots = tuple(path.resolve() for path in allowed_source_roots)
        if not self.allowed_source_roots:
            raise ValueError("At least one source root is required")
        self.image = self._validate_pinned_image(image)
        self.runner = runner or MsconvertCliRunner()

    def convert(self, request: ConversionRequest) -> ConversionResult:
        return self.convert_with_control(request)

    def convert_with_control(
        self,
        request: ConversionRequest,
        cancel_event: threading.Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> ConversionResult:
        started = datetime.now(timezone.utc)
        started_clock = time.monotonic()
        recipe_name = request.recipe
        compiled_arguments: list[str] = []
        job_dir: Path | None = None
        report = ProcessReport(1, "", "")
        try:
            recipe = (
                compile_recipe(request.recipe_definition, request.parameter_overrides)
                if request.recipe_definition
                else get_recipe(recipe_name)
            )
            compiled_arguments = list(recipe.arguments)
            source = self._resolve_source(request.source_path)
            job_dir = self._prepare_job_directory(request.job_id)
            output_dir = job_dir / "output"
            output_dir.mkdir(mode=0o700)
            runtime_recipe = recipe
            if recipe.config_path:
                runtime_config = job_dir / "msconvert-config.txt"
                shutil.copyfile(recipe.config_path, runtime_config)
                runtime_recipe = replace(recipe, config_path=runtime_config)
            report = self.runner.run(
                source,
                output_dir,
                runtime_recipe,
                self.image,
                request.source_name,
                cancel_event,
                progress,
            )
            if report.cancelled:
                raise RuntimeError("Conversion was cancelled")
            if report.return_code != 0:
                detail = report.stderr.strip() or report.stdout.strip()
                message = f"msconvert exited with status {report.return_code}"
                if detail:
                    message = f"{message}: {detail[-4000:]}"
                raise RuntimeError(message)
            if progress:
                progress("validating", 0.9)
            outputs = self._discover_and_validate(output_dir, recipe.output_format, progress)
            finished = datetime.now(timezone.utc)
            return ConversionResult(
                job_id=request.job_id,
                status="succeeded",
                recipe=recipe_name,
                image=self.image,
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                duration_seconds=round(time.monotonic() - started_clock, 6),
                converter_version=report.tool_version,
                command=list(report.command),
                arguments=compiled_arguments,
                outputs=outputs,
                stdout=report.stdout,
                stderr=report.stderr,
                scratch_path=str(job_dir),
            )
        except Exception as error:
            was_cancelled = cancel_event is not None and cancel_event.is_set()
            if job_dir is not None and (was_cancelled or not request.retain_scratch_on_failure):
                shutil.rmtree(job_dir, ignore_errors=True)
            finished = datetime.now(timezone.utc)
            return ConversionResult(
                job_id=request.job_id,
                status="cancelled" if was_cancelled else "failed",
                recipe=recipe_name,
                image=self.image,
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                duration_seconds=round(time.monotonic() - started_clock, 6),
                converter_version=report.tool_version,
                command=list(report.command),
                arguments=compiled_arguments,
                stdout=report.stdout,
                stderr=report.stderr,
                error=str(error),
                scratch_path=str(job_dir) if job_dir and job_dir.exists() else None,
            )

    def _resolve_source(self, source_value: str) -> Path:
        source = Path(source_value).resolve(strict=True)
        if not any(source == root or source.is_relative_to(root) for root in self.allowed_source_roots):
            raise ValueError("Source path is outside configured storage roots")
        if not source.is_file() and not source.is_dir():
            raise ValueError("Source must be a file or vendor bundle directory")
        return source

    def _prepare_job_directory(self, job_id: str) -> Path:
        if not SAFE_JOB_ID.fullmatch(job_id):
            raise ValueError("Job ID contains unsupported characters")
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.scratch_root, 0o700)
        job_dir = self.scratch_root / job_id
        try:
            job_dir.mkdir(mode=0o700)
        except FileExistsError as error:
            raise ValueError("Scratch directory already exists for this job") from error
        return job_dir

    def _discover_and_validate(
        self,
        output_dir: Path,
        output_format: OutputFormat,
        progress: Callable[[str, float], None] | None = None,
    ) -> list[OutputArtifact]:
        suffix = f".{output_format.value.lower()}"
        candidates = sorted(path for path in output_dir.rglob("*") if path.is_file() and path.suffix.lower() == suffix)
        if not candidates:
            raise ValueError(f"No {output_format.value} output was produced")
        artifacts: list[OutputArtifact] = []
        for path in candidates:
            self._validate_file(path, output_format)
            artifact_path = path
            if output_format == OutputFormat.MZML:
                if progress:
                    progress("indexing", 0.95)
                artifact_path = path.with_name(f"{path.name}.gz")
                write_indexed_mzml_gzip(path, artifact_path)
                self._validate_indexed_mzml(artifact_path)
                path.unlink()
            artifacts.append(
                OutputArtifact(
                    path=str(artifact_path),
                    format=output_format.value,
                    byte_size=artifact_path.stat().st_size,
                    sha256=self._sha256(artifact_path),
                )
            )
        return artifacts

    @staticmethod
    def _validate_indexed_mzml(path: Path) -> None:
        try:
            with gzip.open(path, "rb") as stream:
                root_name = None
                for event, element in ElementTree.iterparse(
                    stream, events=("start", "end")
                ):
                    if root_name is None:
                        root_name = element.tag.rsplit("}", 1)[-1]
                    if event == "end":
                        element.clear()
        except (OSError, ElementTree.ParseError) as error:
            raise ValueError(f"Invalid self-indexed mzML gzip: {path.name}") from error
        if root_name not in {"mzML", "indexedmzML"}:
            raise ValueError(f"Unexpected mzML root element in {path.name}")

    @staticmethod
    def _validate_file(path: Path, output_format: OutputFormat) -> None:
        if path.stat().st_size == 0:
            raise ValueError(f"Converter produced an empty file: {path.name}")
        if output_format in {OutputFormat.MZML, OutputFormat.MZXML}:
            try:
                _, root = next(ElementTree.iterparse(path, events=("start",)))
            except (ElementTree.ParseError, StopIteration) as error:
                raise ValueError(f"Invalid {output_format.value} XML: {path.name}") from error
            valid_roots = {"mzML", "indexedmzML"} if output_format == OutputFormat.MZML else {"mzXML"}
            if root.tag.rsplit("}", 1)[-1] not in valid_roots:
                raise ValueError(f"Unexpected {output_format.value} root element in {path.name}")
            return
        prefix = path.read_bytes()[:65536].decode("utf-8", errors="replace")
        if output_format == OutputFormat.MGF and "BEGIN IONS" not in prefix:
            raise ValueError(f"Invalid MGF output: {path.name}")
        if output_format == OutputFormat.MS2:
            data_lines = [line for line in prefix.splitlines() if line and not line.startswith(("H", "#"))]
            if not data_lines or not any(line.startswith("S\t") or line.startswith("S ") for line in data_lines):
                raise ValueError(f"Invalid MS2 output: {path.name}")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_pinned_image(image: str) -> str:
        if not image or image.endswith(":latest"):
            raise ValueError("The ProteoWizard image must use a pinned tag or digest")
        final_segment = image.rsplit("/", 1)[-1]
        if ":" not in final_segment and "@sha256:" not in image:
            raise ValueError("The ProteoWizard image must use a pinned tag or digest")
        return image
