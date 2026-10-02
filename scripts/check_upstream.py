#!/usr/bin/env python3
"""Check independent upstreams; notify via deduplicated GitHub Issues only."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

SCRIPT_REPO = "nelvko/clash-for-linux-install"
CORE_REPO = "MetaCubeX/mihomo"


def api(path, payload=None, paginate=False):
    command = ["gh", "api", path]
    if paginate:
        command += ["--paginate", "--slurp"]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=120, check=True)
    return json.loads(result.stdout)


def version(tag):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        raise ValueError(f"Not a stable version tag: {tag!r}")
    return tuple(map(int, match.groups()))


def issue(component, old, new, repo):
    marker = f"<!-- upstream-watch:{component}:{new} -->"
    target = (f"https://github.com/{repo}/commit/{new}" if component == "clashctl"
              else f"https://github.com/{repo}/releases/tag/{new}")
    return {
        "marker": marker,
        "title": f"[upstream] {component} {new[:12] if component == 'clashctl' else new}",
        "body": (
            f"{marker}\n## 上游更新：{component}\n\n"
            f"- 本仓库 Dockerfile 固定版本：`{old}`\n"
            f"- 检测到的上游版本：`{new}`\n"
            f"- [上游记录]({target})\n"
            f"- [完整变更对比](https://github.com/{repo}/compare/{old}...{new})\n\n"
            "这是一条上游更新通知，尚未更新 Dockerfile、构建或发布镜像，也未更新 NAS。\n"
            "升级前需审查变更和容器补丁兼容性，并通过 amd64、arm64 集成测试。\n\n"
            "同一上游版本只创建一次通知；关闭此 Issue 不会重复创建。"
        ),
    }


def plan_updates(dockerfile, commit, release):
    script_pin = re.search(r'io\.clashctl\.upstream\.revision="([0-9a-f]{40})"', dockerfile)
    core_pin = re.search(r"FROM metacubex/mihomo:(v\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}\b", dockerfile)
    if not script_pin or not core_pin or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Missing or malformed Dockerfile pins / upstream SHA")
    if release["draft"] or release["prerelease"]:
        raise ValueError("Expected a published stable mihomo release")
    new = release["tag_name"]
    updates = []
    if version(new) > version(core_pin[1]):
        updates.append(issue("mihomo", core_pin[1], new, CORE_REPO))
    if script_pin[1] != commit:
        updates.append(issue("clashctl", script_pin[1], commit, SCRIPT_REPO))
    return updates


def unreported(updates, issues):
    bodies = [row.get("body") or "" for row in issues
              if row.get("user", {}).get("login") == "github-actions[bot]"
              and "pull_request" not in row]
    return [u for u in updates if not any(u["marker"] in body for body in bodies)]


def publish(client, repo, updates, dry_run):
    urls = []
    for update in updates:
        if dry_run:
            print(f"Would create: {update['title']}")
            continue
        created = client(f"repos/{repo}/issues", payload={
            "title": update["title"], "body": update["body"],
            "assignees": [], "labels": [],
        })
        actual = client(f"repos/{repo}/issues/{created['number']}")
        if actual["title"] != update["title"] or actual["body"] != update["body"]:
            raise RuntimeError("Created issue failed readback verification")
        urls.append(actual["html_url"])
        print(f"Created and verified: {actual['html_url']}")
    return urls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-notification", action="store_true")
    args = parser.parse_args()
    if not args.repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo owner/name is required")
    root = Path(__file__).resolve().parents[1]
    commit = api(f"repos/{SCRIPT_REPO}/commits?per_page=1")[0]["sha"]
    release = api(f"repos/{CORE_REPO}/releases/latest")
    updates = plan_updates((root / "Dockerfile").read_text(), commit, release)
    if args.test_notification:
        updates.append({
            "marker": "<!-- upstream-watch:notification-test:v1 -->",
            "title": "[upstream] 自动通知连通性测试（非版本更新）",
            "body": "<!-- upstream-watch:notification-test:v1 -->\n"
                    "这是 GitHub Actions 自动创建 Issue 的一次性验证，不表示上游有新版。\n"
                    "未构建或发布镜像，未操作 NAS。验证后可关闭此 Issue。",
        })
    pages = api(f"repos/{args.repo}/issues?state=all&per_page=100", paginate=True)
    pending = unreported(updates, [row for page in pages for row in page])
    urls = publish(api, args.repo, pending, args.dry_run)
    summary = (f"Checked clashctl `{commit}` and mihomo `{release['tag_name']}`.\n"
               f"Unreported updates/test notifications: {len(pending)}. Dry run: {args.dry_run}.\n"
               + "\n".join(urls) + "\n")
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary)


if __name__ == "__main__":
    main()
