# Ephemeral non-root C# / .NET 8 xUnit sandbox image for KI/OONV (Tier 2)
# Build: docker build -t uni-sandbox-csharp:latest docker/sandbox-csharp
# Run strictly isolated: --network=none --read-only --cap-drop=ALL --user 65534:65534

FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_EnableDiagnostics=0 \
    DOTNET_GCHeapCount=1 \
    NUGET_PACKAGES=/opt/nuget-cache \
    HOME=/tmp \
    TMPDIR=/tmp

WORKDIR /opt/sandbox-project

RUN mkdir -p /opt/nuget-cache /opt/sandbox-project /opt/runner

COPY <<'EOF' /opt/sandbox-project/Sandbox.csproj
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
    <IsTestProject>true</IsTestProject>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
  </ItemGroup>
</Project>
EOF

COPY <<'EOF' /opt/sandbox-project/Solution.cs
namespace UniSandbox;

public static class Placeholder
{
    public static int Ping() => 1;
}
EOF

COPY <<'EOF' /opt/sandbox-project/SmokeTests.cs
using Xunit;

namespace UniSandbox;

public class SmokeTests
{
    [Fact]
    public void Warmup_Passes() => Assert.Equal(1, Placeholder.Ping());
}
EOF

# Pre-restore NuGet packages and warm up compiler caches offline.
RUN dotnet restore /opt/sandbox-project/Sandbox.csproj \
    && dotnet build /opt/sandbox-project/Sandbox.csproj --no-restore -c Release \
    && rm -rf /opt/sandbox-project/bin /opt/sandbox-project/obj /opt/sandbox-project/SmokeTests.cs

COPY <<'EOF' /opt/runner/run.py
#!/usr/bin/env python3
"""Read JSON payload from stdin, compile and run xUnit inside /tmp/work."""
import json
import os
import re
import shutil
import subprocess
import sys
import time

SENTINEL = "__UNI_CSHARP_JSON_START__"


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"\n{SENTINEL}\n" + json.dumps({"ok": False, "stderr": str(exc)}))
        return 1

    work_dir = "/tmp/work"
    shutil.copytree("/opt/sandbox-project", work_dir, dirs_exist_ok=True)

    code = str(payload.get("code") or "")
    with open(os.path.join(work_dir, "Solution.cs"), "w", encoding="utf-8") as fh:
        fh.write(code)

    tests = payload.get("tests") or []
    if tests:
        methods = []
        for idx, tc in enumerate(tests, start=1):
            tcode = str(tc.get("code") or "")
            methods.append(f"    [Fact]\n    public void Test_{idx}()\n    {{\n        {tcode}\n    }}")
        test_src = (
            "using System;\nusing System.Linq;\nusing System.Collections.Generic;\n"
            "using Xunit;\n\nnamespace UniSandbox;\n\n"
            "public class GeneratedSandboxTests\n{\n"
            + "\n\n".join(methods)
            + "\n}\n"
        )
        with open(os.path.join(work_dir, "GeneratedTests.cs"), "w", encoding="utf-8") as fh:
            fh.write(test_src)

    t0 = time.monotonic()
    proc = subprocess.run(
        [
            "dotnet",
            "test",
            os.path.join(work_dir, "Sandbox.csproj"),
            "--no-restore",
            "--nologo",
            "-v",
            "normal",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    out_text = proc.stdout or ""
    err_text = proc.stderr or ""
    results = []
    for idx, tc in enumerate(tests, start=1):
        passed = bool(re.search(rf"Passed\s+UniSandbox\.GeneratedSandboxTests\.Test_{idx}\b", out_text))
        failed = bool(re.search(rf"Failed\s+UniSandbox\.GeneratedSandboxTests\.Test_{idx}\b", out_text))
        results.append(
            {
                "name": str(tc.get("name") or f"Test {idx}"),
                "passed": passed and not failed,
                "hidden": bool(tc.get("hidden", False)),
                "elapsed_ms": elapsed_ms,
                "message": "" if passed else ("Failed" if failed else "Build/runtime error"),
            }
        )

    ok = proc.returncode == 0 and all(r["passed"] for r in results)
    summary = {
        "ok": ok,
        "stdout": out_text,
        "stderr": err_text if not ok else "",
        "tests": results,
    }
    print(f"\n{SENTINEL}\n" + json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
EOF

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-minimal \
    && rm -rf /var/lib/apt/lists/* \
    && chmod 0755 /opt/runner/run.py \
    && chmod -R a+rX /opt/nuget-cache /opt/sandbox-project /opt/runner

USER 65534:65534

ENTRYPOINT ["python3", "/opt/runner/run.py"]
