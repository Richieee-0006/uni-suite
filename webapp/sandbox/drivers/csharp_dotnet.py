"""Ephemeral Docker driver for isolated C# / .NET (xUnit / Roslyn) execution."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import time

from ..base import (
    MAX_STDOUT_BYTES,
    ExecutionRequest,
    ExecutionResult,
    LanguageDriver,
    TestResult,
    ValidationError,
    communicate_bounded,
)
from .r_docker import _stripped_host_env

DEFAULT_CSHARP_IMAGE = "uni-sandbox-csharp:latest"
IMAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
RESULT_SENTINEL = "__UNI_CSHARP_JSON_START__"
_CS_MAX_PIPE_STDOUT_BYTES = 256 * 1024
_CS_MAX_PIPE_STDERR_BYTES = 64 * 1024


class CSharpDotnetDriver(LanguageDriver):
    """Tier 2 ephemeral Docker driver for C# .NET (`uni-sandbox-csharp:latest`)."""

    driver_id = "csharp_dotnet"
    language = "csharp"
    display_name = "C# .NET (Docker xUnit)"
    tier = "tier2_docker"
    max_timeout_sec = 10.0

    def __init__(self, image: str | None = None) -> None:
        candidate = (
            image or os.getenv("SANDBOX_CSHARP_IMAGE", DEFAULT_CSHARP_IMAGE)
        ).strip()
        if not IMAGE_NAME_RE.match(candidate):
            raise ValidationError("Invalid SANDBOX_CSHARP_IMAGE identifier.")
        self.image = candidate

    def _docker_binary(self) -> str | None:
        return shutil.which("docker")

    def is_available(self) -> tuple[bool, str]:
        if os.getenv("SANDBOX_DOCKER_ENABLED", "0").strip() != "1":
            return (
                False,
                "Tier 2 Docker sandbox je vypnutý (SANDBOX_DOCKER_ENABLED != 1).",
            )
        docker_bin = self._docker_binary()
        if not docker_bin:
            return False, "Docker CLI nebylo na serveru nalezeno."
        return True, ""

    def build_docker_command(
        self,
        container_name: str,
        docker_bin: str = "docker",
    ) -> list[str]:
        """Construct the hardened ``docker run`` command (512 MB / 0.5 CPU / 10s)."""
        tmpfs_spec = "/tmp:rw,nosuid,size=128m"  # noqa: S108
        return [
            docker_bin,
            "run",
            "--rm",
            "--init",
            "--interactive",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--memory=512m",
            "--memory-swap=512m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--tmpfs",
            tmpfs_spec,
            "--env",
            "HOME=/tmp",  # noqa: S108
            "--env",
            "TMPDIR=/tmp",  # noqa: S108
            "--env",
            "DOTNET_CLI_TELEMETRY_OPTOUT=1",
            "--env",
            "DOTNET_NOLOGO=1",
            "--env",
            "DOTNET_EnableDiagnostics=0",
            "--env",
            "DOTNET_GCHeapCount=1",
            "--env",
            "NUGET_PACKAGES=/opt/nuget-cache",
            self.image,
        ]

    async def _force_remove_container(
        self,
        docker_bin: str,
        container_name: str,
    ) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                docker_bin,
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=_stripped_host_env(),
            )
            await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except (OSError, TimeoutError):
            pass

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        docker_bin = self._docker_binary()
        if not docker_bin:
            return ExecutionResult(
                ok=False,
                status="unavailable",
                stderr="Docker CLI není dostupné.",
                driver=self.driver_id,
            )

        container_name = f"uni-sbx-cs-{secrets.token_hex(8)}"
        run_sentinel = f"__UNI_SBX_CS_{secrets.token_hex(12)}__"
        cmd = self.build_docker_command(container_name, docker_bin=docker_bin)
        payload = json.dumps(
            {
                "code": request.code,
                "tests": [tc.to_dict() for tc in request.tests],
                "sentinel": run_sentinel,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        started = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_stripped_host_env(),
            )
        except OSError as exc:
            return ExecutionResult(
                ok=False,
                status="unavailable",
                stderr=f"Nepodařilo se spustit Docker daemon: {exc}",
                driver=self.driver_id,
            )

        try:
            raw_out, raw_err, exceeded = await communicate_bounded(
                proc,
                payload,
                timeout_sec=request.timeout_sec,
                max_stdout_bytes=_CS_MAX_PIPE_STDOUT_BYTES,
                max_stderr_bytes=_CS_MAX_PIPE_STDERR_BYTES,
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await self._force_remove_container(docker_bin, container_name)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return ExecutionResult(
                ok=False,
                status="timeout",
                stderr=(
                    f"Překročen časový limit běhu ({request.timeout_sec:g} s)."
                ),
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if exceeded:
            await self._force_remove_container(docker_bin, container_name)
            return ExecutionResult(
                ok=False,
                status="error",
                stderr="Výstup sandboxu překročil maximální povolenou velikost.",
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        stdout_text = raw_out.decode("utf-8", errors="replace")
        stderr_text = raw_err.decode("utf-8", errors="replace")

        if run_sentinel in stdout_text:
            _, tail = stdout_text.rsplit(run_sentinel, 1)
            try:
                parsed = json.loads(tail.strip())
                tests_out = [
                    TestResult(
                        name=str(t.get("name", "Test")),
                        passed=bool(t.get("passed", False)),
                        actual=t.get("actual"),
                        expected=t.get("expected"),
                        message=str(t.get("message") or ""),
                        elapsed_ms=int(t.get("elapsed_ms") or 0),
                        hidden=bool(t.get("hidden", False)),
                    )
                    for t in (parsed.get("tests") or [])
                    if isinstance(t, dict)
                ]
                ok = bool(parsed.get("ok", False))
                return ExecutionResult(
                    ok=ok,
                    status="ok" if ok else "error",
                    stdout=str(parsed.get("stdout") or ""),
                    stderr=str(parsed.get("stderr") or ""),
                    tests=tests_out,
                    elapsed_ms=elapsed_ms,
                    driver=self.driver_id,
                )
            except json.JSONDecodeError:
                pass

        low_err = stderr_text.lower()
        if "cannot connect to the docker daemon" in low_err or (
            "unable to find image" in low_err
        ):
            return ExecutionResult(
                ok=False,
                status="unavailable",
                stderr=(
                    f"Docker daemon nebo obraz {self.image} není připraven."
                ),
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        return ExecutionResult(
            ok=proc.returncode == 0,
            status="ok" if proc.returncode == 0 else "error",
            stdout=stdout_text[:MAX_STDOUT_BYTES],
            stderr=stderr_text,
            elapsed_ms=elapsed_ms,
            driver=self.driver_id,
        )
