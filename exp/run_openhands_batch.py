#!/usr/bin/env python3
"""
Batch Patch Generator using OpenHands Headless CLI.
Iterates over SWE artifact directories and saves `generated_patch.diff`.
"""

import os
import re
import json
import shutil
import subprocess
import argparse
from pathlib import Path


def run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout
    )


def solve_issue_with_openhands(
    artifact_dir: Path,
    output_dir: Path,
    repos_cache_dir: Path,
    timeout_seconds: int = 600
):
    issue_folder = artifact_dir.name
    print(f"\n{'='*60}\n[*] Processing: {issue_folder}\n{'='*60}")

    # 1. Parse issue details
    json_files = sorted(list(artifact_dir.glob("issue_*.json"))) or sorted(list(artifact_dir.glob("*.json")))
    if not json_files:
        print(f"[-] Missing issue JSON in {artifact_dir}. Skipping.")
        return

    with open(json_files[0], "r", encoding="utf-8") as f:
        issue_data = json.load(f)

    issue_num = str(issue_data.get("number") or issue_folder)
    linked_prs = issue_data.get("linked_prs", [])
    base_sha = linked_prs[0].get("base_sha") if linked_prs else issue_data.get("base_sha")
    raw_url = issue_data.get("url", "")
    repo_url = (raw_url.split("/issues/")[0] + ".git") if "/issues/" in raw_url else raw_url
    
    title = issue_data.get("title", "")
    body = issue_data.get("body", "")
    problem_statement = f"Issue Title: {title}\n\nDescription:\n{body}"

    # Setup directories
    patch_target_dir = output_dir / f"result_{issue_folder}"
    patch_target_dir.mkdir(parents=True, exist_ok=True)
    patch_file_path = patch_target_dir / "generated_patch.diff"
    log_file_path = patch_target_dir / "openhands_run.log"

    workspace = repos_cache_dir / f"openhands_workspace_{issue_num}"
    if workspace.exists():
        shutil.rmtree(workspace)

    try:
        # 2. Clone and prepare base repo
        print(f"[*] Cloning repository to {workspace} at commit {base_sha}...")
        run_cmd(["git", "clone", repo_url, str(workspace)])
        if base_sha:
            run_cmd(["git", "checkout", base_sha], cwd=workspace)

        # 3. Create task prompt file
        task_prompt = (
            f"You are tasked with fixing the following issue in this repository:\n\n"
            f"{problem_statement}\n\n"
            f"Requirements:\n"
            f"1. Analyze the codebase to locate where the issue occurs.\n"
            f"2. Modify only the necessary files to fix the issue without introducing regressions.\n"
            f"3. Do NOT create extraneous files or commit your changes.\n"
            f"4. Once done, make sure the solution is complete."
        )
        task_file = workspace / ".openhands_task.txt"
        task_file.write_text(task_prompt, encoding="utf-8")

        # 4. Run OpenHands in Headless Mode inside the repo
        print("[*] Running OpenHands in headless mode...")
        openhands_cmd = [
            "openhands",
            "--headless",
            "--always-approve",
            "--override-with-envs",
            "-f", str(task_file.name)
        ]

        res = run_cmd(openhands_cmd, cwd=workspace, timeout=timeout_seconds)
        log_file_path.write_text(f"STDOUT:\n{res.stdout}\n\nSTDERR:\n{res.stderr}", encoding="utf-8")

        # Clean up temporary prompt file
        task_file.unlink(missing_ok=True)

        # 5. Extract Git Diff
        diff_res = run_cmd(["git", "diff"], cwd=workspace)
        patch_content = diff_res.stdout

        if patch_content.strip():
            patch_file_path.write_text(patch_content, encoding="utf-8")
            print(f"[✓] Successfully generated patch at: {patch_file_path}")
        else:
            print("[-] No changes were made by OpenHands (empty diff).")

    except subprocess.TimeoutExpired:
        print(f"[-] Execution timed out after {timeout_seconds}s.")
    finally:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Generate bug fix patches using OpenHands Headless CLI.")
    parser.add_argument("--artifacts-dir", type=Path, required=True, help="Path to artifacts containing issue JSONs.")
    parser.add_argument("--output-dir", type=Path, default=Path("./openhands_patches"), help="Output directory for generated diffs.")
    parser.add_argument("--repos-cache", type=Path, default=Path("./.openhands_repos_cache"), help="Temp repo cache directory.")
    parser.add_argument("--timeout", type=int, default=600, help="Per-issue timeout in seconds.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.repos_cache.mkdir(parents=True, exist_ok=True)

    artifacts = sorted([p for p in args.artifacts_dir.iterdir() if p.is_dir() and not p.name.startswith(".")])
    print(f"Found {len(artifacts)} issues to process with OpenHands.")

    try:
        for art in artifacts:
            solve_issue_with_openhands(art, args.output_dir, args.repos_cache, args.timeout)
    finally:
        if args.repos_cache.exists():
            shutil.rmtree(args.repos_cache, ignore_errors=True)


if __name__ == "__main__":
    main()