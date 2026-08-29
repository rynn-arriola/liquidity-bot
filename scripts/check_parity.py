#!/usr/bin/env python3
"""Safe workstation sync and optional read-only production parity check.

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
import shlex
import subprocess
import sys
from typing import Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT = "/root/liquidity-bot"
DEPLOYED_PATHS = ("src/liquidity_bot", "tests")
LOCAL_PEER_CONFIG = REPO_ROOT / ".local-tools" / "parity_peer.json"


class ParityUnavailable(RuntimeError):
    """A required parity authority could not be read."""


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


def _run(
    command: Sequence[str],
    *,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            cwd=REPO_ROOT,
            capture_output=True,
            text=text,
            check=check,
        )
    except FileNotFoundError as exc:
        raise ParityUnavailable(f"command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = (stderr or "").strip() or f"exit {exc.returncode}"
        raise ParityUnavailable(f"{command[0]} failed: {detail}") from exc


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


def peer_powershell_command(repo: str, sync: bool) -> str:
    """Build the recursion-safe command executed on a Windows peer."""
    parity_arguments = "--sync" if sync else ""
    return "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            f"$parityRepo = {_powershell_literal(repo)}",
            "if (-not (Test-Path -LiteralPath (Join-Path $parityRepo '.git'))) {",
            "  Write-Error ('peer repository not found: ' + $parityRepo)",
            "  exit 2",
            "}",
            "Set-Location -LiteralPath $parityRepo",
            "Remove-Item Env:PARITY_PEER_HOST -ErrorAction SilentlyContinue",
            "Remove-Item Env:PARITY_PEER_REPO -ErrorAction SilentlyContinue",
            f"& python -B scripts/check_parity.py {parity_arguments} --no-peer",
            "exit $LASTEXITCODE",
        )
    )


def peer_parity(
    host: str,
    repo: str,
    *,
    sync: bool,
) -> tuple[int, str]:
    """Run the same safe parity check on an available Windows peer."""
    command = peer_powershell_command(repo, sync)
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
    output = result.stdout.strip()
    if result.stderr.strip():
        output = "\n".join(part for part in (output, result.stderr.strip()) if part)
    return result.returncode, output


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
        if peer_host:
            peer_code, peer_output = peer_parity(
                peer_host,
                peer_repo,
                sync=args.sync,
            )
            print(f"  peer        : {peer_host}")
            if peer_code == 0:
                print("  peer state  : MATCH")
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

        if not args.host:
            print("  production  : not checked (no current server configured)")
            if problems:
                print("\nVERDICT: PARITY DRIFT")
                for problem in problems:
                    print(f"  - {problem}")
                return 1
            if peer_pending:
                print("\nVERDICT: CURRENT WORKSTATION PARITY OK · PEER PENDING")
            elif peer_host:
                print("\nVERDICT: BOTH WORKSTATIONS PARITY OK")
            else:
                print("\nVERDICT: WORKSTATION PARITY OK")
            print("GitHub is the tracked-file source of truth.")
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
