#!/usr/bin/env python3
"""Safe workstation, WSL runtime and private-ledger parity check.

Run this command independently on every workstation.  GitHub is the rendezvous
point between workstations.  When a production host exists, it can additionally
be checked against DEPLOYED_COMMIT rather than being required to equal a newer
docs-only HEAD.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT = "/root/liquidity-bot"
DEPLOYED_PATHS = ("src/liquidity_bot", "tests")
LOCAL_PEER_CONFIG = REPO_ROOT / ".local-tools" / "parity_peer.json"
LOCAL_RUNTIME_CONFIG = REPO_ROOT / ".local-tools" / "parity_runtime.json"
RUNTIME_SNAPSHOT_SCRIPT = REPO_ROOT / "scripts" / "runtime_parity_snapshot.py"
RUNTIME_BACKUP_DIR = REPO_ROOT / "data" / "cold-backup" / "laptop-runtime"
RUNTIME_CAPTURE_TIMEOUT_SECONDS = 180
SAFE_LIVE_RUNTIME_SYNC_PREFIXES = (
    "docs/",
    "tests/",
)
SAFE_LIVE_RUNTIME_SYNC_FILES = {
    "AGENTS.md",
    "AI_OPERATOR.md",
    "BUGFIXES.md",
    "CLAUDE.md",
    "DECISIONS.md",
    "FINDINGS.md",
    "NEXT.md",
    "README.md",
    "STATUS.md",
    "WATCHLIST.md",
    "scripts/check_parity.py",
    "scripts/runtime_parity_snapshot.py",
}


class ParityUnavailable(RuntimeError):
    """A required parity authority could not be read."""


class RuntimePending(ParityUnavailable):
    """The configured active-runtime machine is offline or unreachable."""


def load_peer_config(path: Path = LOCAL_PEER_CONFIG) -> tuple[str, str]:
    """Read optional machine-local peer coordinates without tracking them."""
    if not path.is_file():
        return "", ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityUnavailable(f"invalid peer config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParityUnavailable(f"invalid peer config {path}: expected object")
    host = payload.get("host", "")
    repo = payload.get("repo", "")
    if not isinstance(host, str) or not isinstance(repo, str):
        raise ParityUnavailable(
            f"invalid peer config {path}: host/repo must be strings"
        )
    if bool(host.strip()) != bool(repo.strip()):
        raise ParityUnavailable(
            f"invalid peer config {path}: host and repo must be set together"
        )
    return host.strip(), repo.strip()


def load_runtime_config(path: Path = LOCAL_RUNTIME_CONFIG) -> dict[str, str] | None:
    """Read private coordinates for the one active laptop WSL runtime."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityUnavailable(f"invalid runtime config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParityUnavailable(f"invalid runtime config {path}: expected object")
    allowed = {"host", "windows_repo", "wsl_distro", "wsl_repo", "source_role"}
    unknown = set(payload) - allowed
    if unknown:
        raise ParityUnavailable(
            f"invalid runtime config {path}: unknown field(s) {sorted(unknown)}"
        )
    values = {
        "host": payload.get("host", ""),
        "windows_repo": payload.get("windows_repo", ""),
        "wsl_distro": payload.get("wsl_distro", "Ubuntu-24.04"),
        "wsl_repo": payload.get("wsl_repo", "/home/polybot/liquidity-bot"),
        "source_role": payload.get("source_role", "ACTIVE-LAPTOP"),
    }
    if not all(isinstance(value, str) for value in values.values()):
        raise ParityUnavailable(
            f"invalid runtime config {path}: every value must be a string"
        )
    if not all(
        values[field].strip()
        for field in ("windows_repo", "wsl_distro", "wsl_repo")
    ):
        raise ParityUnavailable(
            f"invalid runtime config {path}: Windows repo, distro and WSL repo are required"
        )
    return {key: value.strip() for key, value in values.items()}


def _run(
    command: Sequence[str],
    *,
    text: bool = True,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            cwd=REPO_ROOT,
            capture_output=True,
            text=text,
            check=check,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ParityUnavailable(f"command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = (stderr or "").strip() or f"exit {exc.returncode}"
        raise ParityUnavailable(f"{command[0]} failed: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParityUnavailable(
            f"{command[0]} timed out after {exc.timeout} seconds"
        ) from exc


def _git(*arguments: str) -> str:
    result = _run(("git", *arguments))
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def _git_bytes(*arguments: str) -> bytes:
    result = _run(("git", *arguments), text=False)
    assert isinstance(result.stdout, bytes)
    return result.stdout


def parse_remote_snapshot(output: str) -> tuple[str, dict[str, str]]:
    """Parse the fixed marker/hash response emitted by the production host."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("DEPLOYED_COMMIT "):
        raise ParityUnavailable("server did not return DEPLOYED_COMMIT")
    marker = lines[0].split(maxsplit=1)[1].strip()
    if not marker:
        raise ParityUnavailable("server returned an empty DEPLOYED_COMMIT")

    manifest: dict[str, str] = {}
    for line in lines[1:]:
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ParityUnavailable(f"invalid server hash row: {line}")
        digest, path = parts
        manifest[path.lstrip("*")] = digest.lower()
    if not manifest:
        raise ParityUnavailable("server returned no source/test hashes")
    return marker, manifest


def commit_manifest(commit: str) -> dict[str, str]:
    paths = _git("ls-tree", "-r", "--name-only", commit, "--", *DEPLOYED_PATHS)
    manifest: dict[str, str] = {}
    for path in paths.splitlines():
        if not path.endswith(".py"):
            continue
        manifest[path] = normalized_digest(
            _git_bytes("show", f"{commit}:{path}")
        )
    if not manifest:
        raise ParityUnavailable(f"commit {commit} has no source/test files")
    return manifest


def normalized_digest(content: bytes) -> str:
    """Hash text identically across Git's Windows/Linux line-end checkouts."""
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def manifest_differences(
    expected: Mapping[str, str], actual: Mapping[str, str]
) -> list[str]:
    differences: list[str] = []
    for path in sorted(expected.keys() - actual.keys()):
        differences.append(f"missing on server: {path}")
    for path in sorted(actual.keys() - expected.keys()):
        differences.append(f"extra on server: {path}")
    for path in sorted(expected.keys() & actual.keys()):
        if expected[path] != actual[path]:
            differences.append(f"content differs: {path}")
    return differences


def remote_snapshot(host: str, key_path: Path) -> tuple[str, dict[str, str]]:
    python_code = (
        "import hashlib; from pathlib import Path; "
        "paths=sorted((*Path('src/liquidity_bot').rglob('*.py'),"
        "*Path('tests').rglob('*.py'))); "
        "[print(hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest(),"
        "p.as_posix()) for p in paths]"
    )
    remote_command = (
        f"cd {REMOTE_ROOT} && "
        "printf 'DEPLOYED_COMMIT ' && head -n 1 DEPLOYED_COMMIT && "
        f"python3 -c {shlex.quote(python_code)}"
    )
    result = _run(
        (
            "ssh",
            "-i",
            str(key_path),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            remote_command,
        )
    )
    assert isinstance(result.stdout, str)
    return parse_remote_snapshot(result.stdout)


def _short(commit: str) -> str:
    return commit[:8]


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def peer_powershell_command(
    repo: str,
    sync: bool,
    *,
    bundle_name: str = "",
) -> str:
    """Build the recursion-safe command executed on a Windows peer."""
    parity_arguments = "--sync" if sync else ""
    rows = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"$parityRepo = {_powershell_literal(repo)}",
        "if (-not (Test-Path -LiteralPath (Join-Path $parityRepo '.git'))) {",
        "  Write-Error ('peer repository not found: ' + $parityRepo)",
        "  exit 2",
        "}",
        "Set-Location -LiteralPath $parityRepo",
        "Remove-Item Env:PARITY_PEER_HOST -ErrorAction SilentlyContinue",
        "Remove-Item Env:PARITY_PEER_REPO -ErrorAction SilentlyContinue",
    ]
    if bundle_name:
        rows.extend(
            (
                f"$parityBundle = Join-Path $HOME {_powershell_literal(bundle_name)}",
                "& git fetch $parityBundle "
                "'refs/remotes/origin/main:refs/remotes/origin/main'",
                "$parityFetchCode = $LASTEXITCODE",
                "if ($parityFetchCode -ne 0) {",
                "  Remove-Item -LiteralPath $parityBundle -Force "
                "-ErrorAction SilentlyContinue",
                "  exit $parityFetchCode",
                "}",
            )
        )
    rows.extend(
        (
            f"& python -B scripts/check_parity.py --no-fetch "
            f"{parity_arguments} --no-peer",
            "$parityCode = $LASTEXITCODE",
        )
    )
    if bundle_name:
        rows.append(
            "Remove-Item -LiteralPath $parityBundle -Force "
            "-ErrorAction SilentlyContinue"
        )
    rows.append("exit $parityCode")
    return "\n".join(rows)


def _peer_head(host: str, repo: str) -> tuple[int, str]:
    command = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            f"$parityRepo = {_powershell_literal(repo)}",
            "if (-not (Test-Path -LiteralPath (Join-Path $parityRepo '.git'))) {",
            "  exit 2",
            "}",
            "& git -C $parityRepo rev-parse HEAD",
            "exit $LASTEXITCODE",
        )
    )
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = _run(
        (
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ),
        check=False,
    )
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        return result.returncode, detail
    for row in result.stdout.splitlines():
        candidate = row.strip()
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", candidate):
            return 0, candidate.lower()
    return 2, "peer did not return a commit id"


def peer_parity(
    host: str,
    repo: str,
    *,
    sync: bool,
    github_head: str,
) -> tuple[int, str]:
    """Run the same safe parity check on an available Windows peer."""
    head_code, head_or_detail = _peer_head(host, repo)
    if head_code:
        return head_code, head_or_detail

    bundle_name = ""
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if head_or_detail != github_head.lower():
        temporary = tempfile.TemporaryDirectory(prefix="liquidity-parity-")
        bundle_path = Path(temporary.name) / "origin-main.bundle"
        _run(("git", "bundle", "create", str(bundle_path), "origin/main"))
        bundle_name = f".liquidity-parity-{uuid.uuid4().hex}.bundle"
        copied = _run(
            (
                "scp",
                "-q",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                str(bundle_path),
                f"{host}:{bundle_name}",
            ),
            check=False,
        )
        assert isinstance(copied.stdout, str)
        assert isinstance(copied.stderr, str)
        if copied.returncode:
            temporary.cleanup()
            return copied.returncode, copied.stderr.strip() or copied.stdout.strip()

    command = peer_powershell_command(
        repo,
        sync,
        bundle_name=bundle_name,
    )
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = _run(
        (
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ),
        check=False,
    )
    if temporary is not None:
        temporary.cleanup()
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    output = result.stdout.strip()
    if result.stderr.strip():
        output = "\n".join(part for part in (output, result.stderr.strip()) if part)
    return result.returncode, output


def _encoded_powershell(
    script: str,
    *,
    host: str = "",
    connect_timeout: int = 15,
    execution_timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    command: list[str] = []
    if host:
        command.extend(
            (
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={connect_timeout}",
                host,
            )
        )
    command.extend(
        (
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        )
    )
    return _run(command, check=False, timeout=execution_timeout)


def _runtime_inspection_script(config: Mapping[str, str], *, fetch: bool) -> str:
    distro = _powershell_literal(config["wsl_distro"])
    repo = _powershell_literal(config["wsl_repo"])
    fetch_row = (
        "Invoke-Wsl @('timeout','45s','git','-C',$runtimeRepo,'fetch','--quiet',$verifiedWindowsRepo,'main:refs/remotes/origin/main')"
        if fetch
        else ""
    )
    return "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            f"$runtimeDistro = {distro}",
            f"$runtimeRepo = {repo}",
            f"$windowsRepo = {_powershell_literal(config['windows_repo'])}",
            "$drive = $windowsRepo.Substring(0,1).ToLowerInvariant()",
            "$tail = $windowsRepo.Substring(2).Replace('\\','/')",
            "$verifiedWindowsRepo = '/mnt/' + $drive + $tail",
            "function Invoke-Wsl([string[]]$CommandArgs) {",
            "  $value = & wsl.exe -d $runtimeDistro -- @CommandArgs 2>&1",
            "  if ($LASTEXITCODE -ne 0) { throw (($value | Out-String).Trim()) }",
            "  return (($value | Out-String).Trim())",
            "}",
            fetch_row,
            "$head = Invoke-Wsl @('git','-C',$runtimeRepo,'rev-parse','HEAD')",
            "$origin = Invoke-Wsl @('git','-C',$runtimeRepo,'rev-parse','origin/main')",
            "$branch = Invoke-Wsl @('git','-C',$runtimeRepo,'branch','--show-current')",
            "$tracked = Invoke-Wsl @('git','-C',$runtimeRepo,'status','--porcelain=v1','--untracked-files=no')",
            "$counts = Invoke-Wsl @('git','-C',$runtimeRepo,'rev-list','--left-right','--count','HEAD...origin/main')",
            "$parts = $counts -split '\\s+'",
            "$changed = @()",
            "if ([int]$parts[1] -gt 0) {",
            "  $rawChanged = Invoke-Wsl @('git','-C',$runtimeRepo,'diff','--name-only','HEAD..origin/main')",
            "  if ($rawChanged) { $changed = @($rawChanged -split \"`r?`n\") }",
            "}",
            "$marker = Invoke-Wsl @('cat',($runtimeRepo + '/DEPLOYED_COMMIT'))",
            "$deployChanged = Invoke-Wsl @('git','-C',$runtimeRepo,'diff','--name-only',($marker + '..HEAD'),'--','src/liquidity_bot','pyproject.toml')",
            "$state = [ordered]@{",
            "  head = $head; origin_head = $origin; deployed_commit = $marker;",
            "  branch = $branch; tracked_status = $tracked;",
            "  ahead = [int]$parts[0]; behind = [int]$parts[1];",
            "  changed_from_github = @($changed);",
            "  deployed_runtime_changes = @($deployChanged -split \"`r?`n\" | Where-Object { $_ });",
            "}",
            "$state | ConvertTo-Json -Compress -Depth 4",
        )
    )


def _parse_runtime_json(output: str) -> dict[str, object]:
    for row in reversed(output.splitlines()):
        candidate = row.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "head" in payload:
            return payload
    raise ParityUnavailable("WSL runtime did not return a state record")


def _runtime_state(
    config: Mapping[str, str], *, fetch: bool
) -> dict[str, object]:
    result = _encoded_powershell(
        _runtime_inspection_script(config, fetch=fetch), host=config["host"]
    )
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        if config["host"] and result.returncode == 255:
            raise RuntimePending(f"active runtime host is unreachable: {detail[-300:]}")
        raise ParityUnavailable(f"WSL runtime inspection failed: {detail[-500:]}")
    return _parse_runtime_json(result.stdout)


def _runtime_path_is_live_safe(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized in SAFE_LIVE_RUNTIME_SYNC_FILES or normalized.startswith(
        SAFE_LIVE_RUNTIME_SYNC_PREFIXES
    )


def _sync_runtime_checkout(config: Mapping[str, str]) -> None:
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            f"$runtimeDistro = {_powershell_literal(config['wsl_distro'])}",
            f"$runtimeRepo = {_powershell_literal(config['wsl_repo'])}",
            "& wsl.exe -d $runtimeDistro -- git -C $runtimeRepo merge --ff-only origin/main",
            "exit $LASTEXITCODE",
        )
    )
    result = _encoded_powershell(script, host=config["host"])
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ParityUnavailable(f"WSL safe fast-forward failed: {detail[-500:]}")


def runtime_checkout_parity(
    config: Mapping[str, str], *, sync: bool
) -> tuple[dict[str, object], str]:
    state = _runtime_state(config, fetch=sync)
    action = "none"
    changed = [str(path) for path in state.get("changed_from_github", [])]
    safe_changes = all(_runtime_path_is_live_safe(path) for path in changed)
    if (
        sync
        and state.get("branch") == "main"
        and not state.get("tracked_status")
        and int(state.get("ahead", 0)) == 0
        and int(state.get("behind", 0)) > 0
        and safe_changes
    ):
        _sync_runtime_checkout(config)
        action = f"fast-forwarded {state['behind']} live-safe commit(s)"
        state = _runtime_state(config, fetch=False)
    elif sync and int(state.get("behind", 0)) > 0 and not safe_changes:
        action = "blocked: GitHub changes require reviewed deployment"
    return state, action


def _capture_runtime_script(config: Mapping[str, str]) -> str:
    return "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            f"$runtimeDistro = {_powershell_literal(config['wsl_distro'])}",
            f"$runtimeRepo = {_powershell_literal(config['wsl_repo'])}",
            "$windowsOutput = Join-Path $HOME '.liquidity-bot\\parity'",
            "New-Item -ItemType Directory -Force -Path $windowsOutput | Out-Null",
            "$drive = $windowsOutput.Substring(0,1).ToLowerInvariant()",
            "$tail = $windowsOutput.Substring(2).Replace('\\','/')",
            "$wslOutput = '/mnt/' + $drive + $tail",
            "& wsl.exe -d $runtimeDistro --cd $runtimeRepo -- .venv/bin/python -B scripts/runtime_parity_snapshot.py create --repo $runtimeRepo --output-dir $wslOutput "
            f"--source-role {_powershell_literal(config['source_role'])}",
            "exit $LASTEXITCODE",
        )
    )


def capture_runtime_ledger(
    config: Mapping[str, str], *, create: bool = True
) -> dict[str, object]:
    if create:
        result = _encoded_powershell(
            _capture_runtime_script(config),
            host=config["host"],
            connect_timeout=30,
            execution_timeout=RUNTIME_CAPTURE_TIMEOUT_SECONDS,
        )
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            if config["host"] and result.returncode == 255:
                raise RuntimePending(
                    f"active runtime host is unreachable: {detail[-300:]}"
                )
            raise ParityUnavailable(f"ledger online backup failed: {detail[-500:]}")

    with tempfile.TemporaryDirectory(prefix="ledger-parity-transfer-") as raw:
        transfer = Path(raw)
        if config["host"]:
            for name in ("commissioning-latest.db", "commissioning-latest.json"):
                copied = _run(
                    (
                        "scp",
                        "-q",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=10",
                        f"{config['host']}:.liquidity-bot/parity/{name}",
                        str(transfer / name),
                    ),
                    check=False,
                )
                assert isinstance(copied.stdout, str)
                assert isinstance(copied.stderr, str)
                if copied.returncode:
                    detail = copied.stderr.strip() or copied.stdout.strip()
                    if copied.returncode == 255:
                        raise RuntimePending(
                            f"active runtime host became unreachable: {detail[-300:]}"
                        )
                    raise ParityUnavailable(f"ledger transfer failed: {detail[-500:]}")
        else:
            source = Path.home() / ".liquidity-bot" / "parity"
            for name in ("commissioning-latest.db", "commissioning-latest.json"):
                shutil.copy2(source / name, transfer / name)

        installed = _run(
            (
                sys.executable,
                "-B",
                str(RUNTIME_SNAPSHOT_SCRIPT),
                "install",
                "--source-dir",
                str(transfer),
                "--output-dir",
                str(RUNTIME_BACKUP_DIR),
            ),
            check=False,
        )
        assert isinstance(installed.stdout, str)
        assert isinstance(installed.stderr, str)
        if installed.returncode:
            detail = installed.stderr.strip() or installed.stdout.strip()
            raise ParityUnavailable(f"ledger verification/install failed: {detail[-500:]}")
        try:
            payload = json.loads(installed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise ParityUnavailable("ledger installer returned invalid evidence") from exc
        if not isinstance(payload, dict):
            raise ParityUnavailable("ledger installer returned invalid evidence")
        return payload


def workstation_problems(worktree: str, local_matches: bool) -> list[str]:
    """Return actionable local/GitHub parity failures."""
    problems: list[str] = []
    if worktree:
        problems.append("workstation has local changes")
        for row in worktree.splitlines()[:10]:
            problems.append(f"  {row}")
    if not local_matches:
        problems.append(
            "workstation HEAD does not equal the GitHub source of truth"
        )
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify this workstation against GitHub and, when supplied, "
            "production against its deployed commit. Makes no venue request "
            "and never controls the bot."
        )
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("DROPLET_HOST", ""),
        help="SSH destination such as user@host (or DROPLET_HOST)",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=Path(
            os.environ.get(
                "DROPLET_KEY", str(Path.home() / ".ssh" / "makerlab_droplet")
            )
        ),
        help="SSH private-key path (or DROPLET_KEY)",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="use the existing origin/main ref instead of refreshing it",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="fast-forward a clean, behind-only main checkout to origin/main",
    )
    parser.add_argument(
        "--peer-host",
        default=os.environ.get("PARITY_PEER_HOST", ""),
        help=(
            "optional SSH alias for the other Windows workstation; defaults "
            "to PARITY_PEER_HOST or .local-tools/parity_peer.json"
        ),
    )
    parser.add_argument(
        "--peer-repo",
        default=os.environ.get("PARITY_PEER_REPO", ""),
        help=(
            "repository path on the peer; defaults to PARITY_PEER_REPO or "
            ".local-tools/parity_peer.json"
        ),
    )
    parser.add_argument(
        "--no-peer",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-runtime",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    print("Liquidity bot machine parity")
    print(f"  workstation : {platform.node() or 'unknown'}")

    try:
        configured_peer_host, configured_peer_repo = (
            ("", "") if args.no_peer else load_peer_config()
        )
        peer_host = args.peer_host or configured_peer_host
        peer_repo = args.peer_repo or configured_peer_repo
        if bool(peer_host) != bool(peer_repo):
            raise ParityUnavailable(
                "peer host and repository must be configured together"
            )
        if args.no_peer:
            peer_host = peer_repo = ""

        if not args.no_fetch:
            _git("fetch", "--quiet", "origin", "main")

        branch = _git("branch", "--show-current") or "detached"
        local_head = _git("rev-parse", "HEAD")
        github_head = _git("rev-parse", "origin/main")
        worktree = _git("status", "--porcelain=v1")
        ahead_text, behind_text = _git(
            "rev-list", "--left-right", "--count", "HEAD...origin/main"
        ).split()
        ahead, behind = int(ahead_text), int(behind_text)
        sync_action = "none"
        if args.sync and not worktree and branch == "main" and behind and not ahead:
            _git("merge", "--ff-only", "origin/main")
            sync_action = f"fast-forwarded {behind} commit(s) from GitHub"
            local_head = _git("rev-parse", "HEAD")
            github_head = _git("rev-parse", "origin/main")
            ahead = behind = 0
        local_matches = local_head == github_head
        worktree_clean = not worktree

        if ahead and behind:
            relation = f"DIVERGED ({ahead} local / {behind} GitHub commit(s))"
        elif ahead:
            relation = f"LOCAL AHEAD ({ahead} unshared commit(s))"
        elif behind:
            relation = f"LOCAL BEHIND ({behind} commit(s))"
        else:
            relation = "MATCH"

        print(f"  branch      : {branch}")
        print(f"  local HEAD  : {_short(local_head)}")
        print(f"  GitHub HEAD : {_short(github_head)}")
        print(f"  sync action : {sync_action}")
        print(
            "  local files : "
            + ("clean" if worktree_clean else "DRIFT (uncommitted/untracked files)")
        )
        print(
            "  local/GitHub: " + relation
        )

        problems = workstation_problems(worktree, local_matches)
        peer_pending = False
        peer_runtime_snapshot_ready = False
        if peer_host:
            peer_code, peer_output = peer_parity(
                peer_host,
                peer_repo,
                sync=args.sync,
                github_head=github_head,
            )
            print(f"  peer        : {peer_host}")
            if peer_code == 0:
                print("  peer state  : MATCH")
                peer_runtime_snapshot_ready = (
                    "ledger copy : VERIFIED PRIVATE RECOVERY COPY" in peer_output
                )
            elif peer_code == 1:
                print("  peer state  : DRIFT")
                problems.append(
                    "reachable peer has dirty, ahead, diverged or mismatched state"
                )
                for row in peer_output.splitlines()[-12:]:
                    problems.append(f"  peer: {row}")
            else:
                peer_pending = True
                print("  peer state  : UNAVAILABLE (pending; preserved)")
                if peer_output:
                    print(f"  peer detail : {peer_output.splitlines()[-1][:160]}")
        else:
            print("  peer        : not configured (run separately on other workstation)")

        runtime_pending = False
        runtime_config = None if args.no_runtime else load_runtime_config()
        if runtime_config is None:
            if args.no_runtime:
                print("  WSL runtime : intentionally skipped")
            else:
                runtime_pending = True
                print("  WSL runtime : not configured (pending)")
        else:
            try:
                runtime_state, runtime_action = runtime_checkout_parity(
                    runtime_config, sync=args.sync
                )
                runtime_head = str(runtime_state["head"])
                runtime_origin = str(runtime_state["origin_head"])
                runtime_marker = str(runtime_state["deployed_commit"])
                runtime_ahead = int(runtime_state.get("ahead", 0))
                runtime_behind = int(runtime_state.get("behind", 0))
                runtime_dirty = bool(runtime_state.get("tracked_status"))
                deploy_changes = [
                    str(path)
                    for path in runtime_state.get("deployed_runtime_changes", [])
                ]
                print(
                    "  WSL runtime : "
                    + (runtime_config["host"] or "this workstation")
                )
                print(f"  WSL HEAD    : {_short(runtime_head)}")
                print(f"  loaded mark : {_short(runtime_marker)}")
                print(f"  WSL action  : {runtime_action}")
                if runtime_dirty:
                    problems.append("WSL runtime has tracked local changes")
                if runtime_ahead or runtime_behind or runtime_head != runtime_origin:
                    problems.append(
                        "WSL runtime checkout does not equal GitHub "
                        f"({runtime_ahead} ahead / {runtime_behind} behind)"
                    )
                if deploy_changes:
                    problems.append(
                        "WSL source differs from DEPLOYED_COMMIT; reviewed deployment required"
                    )
                    problems.extend(f"  {path}" for path in deploy_changes[:20])
                if args.sync:
                    ledger = capture_runtime_ledger(
                        runtime_config,
                        create=not (
                            runtime_config["host"] == peer_host
                            and peer_runtime_snapshot_ready
                        ),
                    )
                    database = ledger.get("database", {})
                    if not isinstance(database, dict):
                        raise ParityUnavailable(
                            "ledger snapshot returned no database evidence"
                        )
                    print("  ledger copy : VERIFIED PRIVATE RECOVERY COPY")
                    print(f"  captured UTC: {ledger.get('created_at', 'unknown')}")
                    print(
                        "  ledger hash : "
                        + str(database.get("sha256", "unknown"))[:12]
                    )
                    print(
                        "  ledger bytes: "
                        + str(database.get("bytes", "unknown"))
                    )
            except RuntimePending as exc:
                runtime_pending = True
                print("  WSL runtime : UNAVAILABLE (pending; preserved)")
                print(f"  runtime info: {str(exc)[-160:]}")
            except ParityUnavailable as exc:
                print("  WSL runtime : FAILED")
                print(f"  runtime info: {str(exc)[-160:]}")
                problems.append(f"runtime/ledger parity failed: {exc}")

        if not args.host:
            print("  production  : not checked (no current server configured)")
            if problems:
                print("\nVERDICT: PARITY DRIFT")
                for problem in problems:
                    print(f"  - {problem}")
                return 1
            if peer_pending or runtime_pending:
                pending = []
                if peer_pending:
                    pending.append("PEER")
                if runtime_pending:
                    pending.append("RUNTIME/LEDGER")
                print(
                    "\nVERDICT: CURRENT WORKSTATION PARITY OK · "
                    + "/".join(pending)
                    + " PENDING"
                )
            elif peer_host:
                print("\nVERDICT: WORKSTATIONS/RUNTIME/LEDGER PARITY OK")
            else:
                print("\nVERDICT: WORKSTATION PARITY OK")
            print(
                "GitHub is tracked-file truth; the verified private ledger copy "
                "is never committed."
            )
            return 0
        if not args.key.is_file():
            raise ParityUnavailable(f"SSH key not found: {args.key}")

        marker, server_manifest = remote_snapshot(args.host, args.key)
        _git("cat-file", "-e", f"{marker}^{{commit}}")
        expected_manifest = commit_manifest(marker)
        server_differences = manifest_differences(
            expected_manifest, server_manifest
        )
        changed_after_deploy = _git(
            "diff", "--name-only", marker, "origin/main", "--", *DEPLOYED_PATHS
        ).splitlines()
        non_runtime_ahead = _git(
            "diff", "--name-only", marker, "origin/main"
        ).splitlines()
        non_runtime_ahead = [
            path
            for path in non_runtime_ahead
            if not any(
                path == prefix or path.startswith(f"{prefix}/")
                for prefix in DEPLOYED_PATHS
            )
        ]

        print(f"  server mark : {_short(marker)}")
        print(
            "  server files: "
            + ("MATCH marker" if not server_differences else "DRIFT")
        )
        print(
            "  deploy lag  : "
            + ("none in src/tests" if not changed_after_deploy else "DRIFT")
        )
        if non_runtime_ahead:
            print(
                f"  note        : {len(non_runtime_ahead)} non-runtime file(s) "
                "are newer on GitHub; this is allowed"
            )

        problems.extend(server_differences)
        if changed_after_deploy:
            problems.append("GitHub has undeployed source/test changes")
            problems.extend(f"  {path}" for path in changed_after_deploy[:20])

        if problems:
            print("\nVERDICT: PARITY DRIFT")
            for problem in problems:
                print(f"  - {problem}")
            return 1

        if peer_pending:
            print("\nVERDICT: CURRENT WORKSTATION/SERVER OK · PEER PENDING")
        else:
            print("\nVERDICT: PARITY OK")
        print("GitHub is the tracked-file source of truth.")
        return 0
    except ParityUnavailable as exc:
        print(f"\nVERDICT: PARITY UNKNOWN\n  - {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
