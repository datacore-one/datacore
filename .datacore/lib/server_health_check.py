#!/usr/bin/env python3
"""
Server Health Check - Weekly System Health Report
Generates comprehensive health metrics for Datacore installation
"""

import os
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
import json


def run_command(cmd, cwd=None):
    """Run shell command and return output"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "Command timed out", 1
    except Exception as e:
        return f"Error: {str(e)}", 1


def get_disk_usage():
    """Get disk usage for key directories"""
    usage_info = []

    # Get overall disk usage
    total, used, free = shutil.disk_usage("/")
    usage_info.append({
        "path": "/",
        "total_gb": total / (1024**3),
        "used_gb": used / (1024**3),
        "free_gb": free / (1024**3),
        "percent_used": (used / total) * 100
    })

    # Get usage for Data directory
    data_path = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
    if data_path.exists():
        try:
            total, used, free = shutil.disk_usage(data_path)
            usage_info.append({
                "path": str(data_path),
                "total_gb": total / (1024**3),
                "used_gb": used / (1024**3),
                "free_gb": free / (1024**3),
                "percent_used": (used / total) * 100
            })
        except Exception as e:
            usage_info.append({
                "path": str(data_path),
                "error": str(e)
            })

    return usage_info


def get_git_status():
    """Get git status for all repositories"""
    data_path = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
    repos = []

    # Find all .git directories
    for item in data_path.iterdir():
        if item.is_dir() and (item / ".git").exists():
            repo_info = {
                "name": item.name,
                "path": str(item)
            }

            # Get branch
            branch_output, _ = run_command("git branch --show-current", cwd=item)
            repo_info["branch"] = branch_output

            # Get status
            status_output, _ = run_command("git status --porcelain", cwd=item)
            repo_info["has_changes"] = bool(status_output)
            repo_info["change_count"] = len(status_output.split("\n")) if status_output else 0

            # Get unpushed commits
            unpushed_output, _ = run_command(
                f"git log origin/{branch_output}..HEAD --oneline 2>/dev/null || echo ''",
                cwd=item
            )
            repo_info["unpushed_commits"] = len(unpushed_output.split("\n")) if unpushed_output else 0

            # Get last commit
            last_commit, _ = run_command(
                "git log -1 --format='%h - %s (%ar)' 2>/dev/null || echo 'No commits'",
                cwd=item
            )
            repo_info["last_commit"] = last_commit

            repos.append(repo_info)

    return repos


def get_running_services():
    """Get list of running datacore services"""
    services = []

    # Check for systemd user services
    systemd_output, _ = run_command(
        "systemctl --user list-units --type=service --state=running 2>/dev/null || echo 'systemd not available'"
    )

    # Check for relevant processes
    process_patterns = [
        ("nightshift", "Nightshift AI Task Executor"),
        ("telegram.*bot", "Telegram Bot"),
        ("apiframe", "API Framework"),
        ("datacore.*server", "Datacore Server"),
    ]

    for pattern, description in process_patterns:
        ps_output, _ = run_command(
            f"ps aux | grep -E '{pattern}' | grep -v grep || echo ''"
        )

        if ps_output:
            lines = ps_output.strip().split("\n")
            for line in lines:
                if line:
                    parts = line.split()
                    if len(parts) >= 11:
                        services.append({
                            "description": description,
                            "pid": parts[1],
                            "cpu_percent": parts[2],
                            "mem_percent": parts[3],
                            "command": " ".join(parts[10:])
                        })

    return services


def get_process_start_times():
    """Get start times for key services"""
    start_times = []

    process_patterns = [
        ("nightshift", "Nightshift"),
        ("telegram.*bot", "Telegram Bot"),
    ]

    for pattern, description in process_patterns:
        ps_output, _ = run_command(
            f"ps -eo pid,lstart,cmd | grep -E '{pattern}' | grep -v grep || echo ''"
        )

        if ps_output:
            lines = ps_output.strip().split("\n")
            for line in lines:
                if line:
                    # Parse: PID    START_TIME    COMMAND
                    parts = line.strip().split(None, 6)
                    if len(parts) >= 6:
                        start_times.append({
                            "service": description,
                            "pid": parts[0],
                            "start_time": " ".join(parts[1:6]),
                            "command": parts[6] if len(parts) > 6 else ""
                        })

    return start_times


def get_expected_services():
    """Define expected services that should be running"""
    return [
        {
            "name": "Nightshift",
            "pattern": "nightshift",
            "required": True,
            "description": "AI task executor for overnight processing"
        },
        {
            "name": "Telegram Bot",
            "pattern": "telegram",
            "required": False,
            "description": "Telegram integration for CRM and notifications"
        }
    ]


def check_service_health():
    """Compare running services against expected services"""
    expected = get_expected_services()
    running = get_running_services()

    health_status = []

    for service in expected:
        # Check if any running service matches the pattern (case-insensitive)
        is_running = any(
            service["pattern"].lower() in s["command"].lower() or
            service["pattern"].lower() in s["description"].lower()
            for s in running
        )

        health_status.append({
            "name": service["name"],
            "expected": True,
            "running": is_running,
            "required": service["required"],
            "status": "OK" if is_running else ("MISSING" if service["required"] else "OPTIONAL_MISSING"),
            "description": service["description"]
        })

    return health_status


def generate_markdown_report(output_path):
    """Generate markdown health report"""
    timestamp = datetime.now(timezone.utc)

    # Collect all data
    disk_usage = get_disk_usage()
    git_repos = get_git_status()
    running_services = get_running_services()
    process_times = get_process_start_times()
    service_health = check_service_health()

    # Build markdown report
    md_lines = [
        "# Server Health Check Report",
        "",
        f"**Generated:** {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
    ]

    # Summary stats
    total_repos = len(git_repos)
    dirty_repos = sum(1 for r in git_repos if r["has_changes"])
    unpushed_repos = sum(1 for r in git_repos if r["unpushed_commits"] > 0)
    running_count = len(running_services)
    missing_required = sum(1 for s in service_health if s["required"] and not s["running"])

    md_lines.extend([
        f"- **Repositories:** {total_repos} total, {dirty_repos} with uncommitted changes, {unpushed_repos} with unpushed commits",
        f"- **Services:** {running_count} running, {missing_required} required services missing",
        f"- **Disk Usage:** {disk_usage[0]['percent_used']:.1f}% used on root partition",
        "",
    ])

    # Health alerts
    alerts = []
    if dirty_repos > 0:
        alerts.append(f"⚠️ {dirty_repos} repositories have uncommitted changes")
    if unpushed_repos > 0:
        alerts.append(f"⚠️ {unpushed_repos} repositories have unpushed commits")
    if missing_required > 0:
        alerts.append(f"❌ {missing_required} required services are not running")
    if disk_usage[0]['percent_used'] > 80:
        alerts.append(f"⚠️ Disk usage is above 80%")

    if alerts:
        md_lines.extend([
            "### Alerts",
            "",
            *[f"{alert}" for alert in alerts],
            "",
        ])
    else:
        md_lines.extend([
            "✅ **All systems healthy - no alerts**",
            "",
        ])

    md_lines.append("---")
    md_lines.append("")

    # Disk Usage Section
    md_lines.extend([
        "## Disk Usage",
        "",
        "| Path | Total (GB) | Used (GB) | Free (GB) | % Used | Status |",
        "|------|------------|-----------|-----------|--------|--------|",
    ])

    for usage in disk_usage:
        if "error" in usage:
            md_lines.append(f"| {usage['path']} | - | - | - | - | Error: {usage['error']} |")
        else:
            status = "⚠️ High" if usage['percent_used'] > 80 else "✅ OK"
            md_lines.append(
                f"| {usage['path']} | {usage['total_gb']:.1f} | {usage['used_gb']:.1f} | "
                f"{usage['free_gb']:.1f} | {usage['percent_used']:.1f}% | {status} |"
            )

    md_lines.extend(["", "---", ""])

    # Git Repository Status
    md_lines.extend([
        "## Git Repository Status",
        "",
        f"**Total Repositories:** {total_repos}",
        "",
        "| Repository | Branch | Status | Unpushed | Last Commit |",
        "|------------|--------|--------|----------|-------------|",
    ])

    for repo in sorted(git_repos, key=lambda x: x["name"]):
        status = "⚠️ Changes" if repo["has_changes"] else "✅ Clean"
        unpushed = f"⚠️ {repo['unpushed_commits']}" if repo['unpushed_commits'] > 0 else "✅ Synced"

        md_lines.append(
            f"| {repo['name']} | {repo['branch']} | {status} | {unpushed} | {repo['last_commit']} |"
        )

    md_lines.extend(["", "---", ""])

    # Service Health
    md_lines.extend([
        "## Service Health Check",
        "",
        "| Service | Expected | Running | Status | Description |",
        "|---------|----------|---------|--------|-------------|",
    ])

    for service in service_health:
        status_icon = {
            "OK": "✅",
            "MISSING": "❌",
            "OPTIONAL_MISSING": "⚠️"
        }.get(service["status"], "❓")

        md_lines.append(
            f"| {service['name']} | {'Yes' if service['expected'] else 'No'} | "
            f"{'Yes' if service['running'] else 'No'} | {status_icon} {service['status']} | "
            f"{service['description']} |"
        )

    md_lines.extend(["", "---", ""])

    # Running Services Detail
    if running_services:
        md_lines.extend([
            "## Running Services (Detail)",
            "",
            "| Service | PID | CPU % | MEM % | Command |",
            "|---------|-----|-------|-------|---------|",
        ])

        for service in running_services:
            cmd_short = service["command"][:80] + "..." if len(service["command"]) > 80 else service["command"]
            md_lines.append(
                f"| {service['description']} | {service['pid']} | {service['cpu_percent']} | "
                f"{service['mem_percent']} | `{cmd_short}` |"
            )

        md_lines.extend(["", "---", ""])

    # Process Start Times
    if process_times:
        md_lines.extend([
            "## Process Start Times",
            "",
            "| Service | PID | Start Time |",
            "|---------|-----|------------|",
        ])

        for proc in process_times:
            md_lines.append(
                f"| {proc['service']} | {proc['pid']} | {proc['start_time']} |"
            )

        md_lines.extend(["", "---", ""])

    # System Information
    uptime_output, _ = run_command("uptime -p")
    kernel_output, _ = run_command("uname -r")

    md_lines.extend([
        "## System Information",
        "",
        f"- **System Uptime:** {uptime_output}",
        f"- **Kernel Version:** {kernel_output}",
        f"- **Report Generated:** {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "---",
        "",
        "## Recommendations",
        "",
    ])

    # Generate recommendations
    recommendations = []

    if dirty_repos > 0:
        recommendations.append(
            f"- Review and commit changes in {dirty_repos} repositories with uncommitted changes"
        )

    if unpushed_repos > 0:
        recommendations.append(
            f"- Push commits in {unpushed_repos} repositories to sync with remote"
        )

    if missing_required > 0:
        missing_services = [s["name"] for s in service_health if s["required"] and not s["running"]]
        recommendations.append(
            f"- Start required services: {', '.join(missing_services)}"
        )

    if disk_usage[0]['percent_used'] > 80:
        recommendations.append(
            "- Disk usage above 80% - consider cleanup or expansion"
        )

    if not recommendations:
        recommendations.append("✅ No immediate actions required - system is healthy")

    md_lines.extend(recommendations)
    md_lines.extend(["", ""])

    # Write report
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        f.write("\n".join(md_lines))

    return output_file


if __name__ == "__main__":
    # Generate report in inbox
    timestamp = datetime.now().strftime("%Y-%m-%d")
    datacore_root = os.environ.get("DATACORE_ROOT", str(Path.home() / "Data"))
    output_path = f"{datacore_root}/0-inbox/server-health-check-{timestamp}.md"

    report_file = generate_markdown_report(output_path)

    print(f"Health check report generated: {report_file}")
    print(f"File size: {report_file.stat().st_size} bytes")
