#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 4 分级动作 —— Cloudflare 模式：干净放行、critical 阻断、其余蒸馏呈现。

消费 triage.py 输出的 triaged.json：
  - critical（且在 block_on 名单）→ commit status = failure（阻断合并）
  - 其余 → commit status = success + 一条蒸馏后的 PR 评论
  - 评论内嵌 👍/👎 提示，反馈经 noise_ledger.py vote 沉淀（Stage 6 闭环）

用法：
  GH 环境:  python3 post_review.py --triaged triaged.json --repo owner/name --pr 123 --sha <sha>
  本地:     python3 post_review.py --triaged triaged.json --dry-run
依赖：仅标准库；gh 模式需要 gh CLI + GH_TOKEN/GITHUB_TOKEN
"""
import argparse
import json
import os
import subprocess
import sys

EMOJI = {"critical": "🔴", "medium": "🟡", "low": "🟢"}


def render_comment(triaged):
    """蒸馏后的 PR 评论（人眼可见的全部内容就这些）。"""
    lines = ["## 🤖 AI 审查（分诊后）", "", f"> {triaged.get('summary', '')}", ""]
    findings = triaged.get("findings", [])
    if not findings:
        lines.append("✅ **AI: no blocking findings** —— 未发现阻断性问题，请 reviewer 关注设计与业务正确性。")
    for f in findings:
        sev = f.get("severity", "low")
        lines += [
            f"{EMOJI.get(sev, '🟢')} **[{sev.upper()}] {f.get('title', '')}**（confidence {f.get('confidence', '?')}）",
            f"- 位置：`{f.get('file', '?')}:{f.get('line', '?')}`",
            f"- 证据：{f.get('evidence', '—')}",
            f"- 建议：{f.get('fix', '—')}",
            "",
        ]
    archived = triaged.get("archived", [])
    if archived:
        lines += ["<details>", f"<summary>已归档 {len(archived)} 条（去重/台账/证据不足，可展开核查）</summary>", ""]
        lines += [f"- {a.get('title', '?')}（{a.get('reason', '?')}）" for a in archived]
        lines += ["", "</details>"]
    lines += ["---",
              "*反馈闭环：对以上每条发现，有用点 👍 / 误报点 👎（本条评论的 reactions），"
              "每周经 `noise_ledger.py add` 沉淀为分诊规则。*"]
    return "\n".join(lines)


def gh_api(endpoint, payload=None, method=None):
    cmd = ["gh", "api", "-X", method or ("POST" if payload else "GET"), endpoint]
    if payload is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else "",
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"警告：gh api {endpoint} 失败：{proc.stderr.strip()}", file=sys.stderr)
    return proc.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Stage 4 分级动作")
    ap.add_argument("--triaged", default="triaged.json")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--repo", help="owner/name（gh 模式）")
    ap.add_argument("--pr", type=int, help="PR 号（gh 模式）")
    ap.add_argument("--sha", help="head commit（设置 status 用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印动作与评论，不调 gh")
    ap.add_argument("--report", help="本地模式：把蒸馏报告写入该 markdown 文件")
    args = ap.parse_args()

    with open(args.triaged, encoding="utf-8") as f:
        triaged = json.load(f)
    block_on = ["critical"]
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            block_on = json.load(f).get("actions", {}).get("block_on", block_on)

    blocking = [f for f in triaged.get("findings", []) if f.get("severity") in block_on]
    state = "failure" if blocking else "success"
    comment = render_comment(triaged)

    print(f"[actions] 分级结果：{state}（阻断 {len(blocking)} 条 critical）")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(comment + "\n")
        print(f"[actions] 报告已写入：{args.report}")
        return
    if args.dry_run:
        print("\n----- 将发布的 PR 评论 -----\n" + comment)
        return

    if not (args.repo and args.pr):
        sys.exit("错误：gh 模式需要 --repo owner/name --pr N [--sha]")
    ctx = "ai-review/triage"
    if args.sha:
        gh_api(f"repos/{args.repo}/statuses/{args.sha}",
               {"state": state, "context": ctx,
                "description": f"{len(blocking)} blocking / {len(triaged.get('findings', []))} findings"},
               method="POST")
    gh_api(f"repos/{args.repo}/issues/{args.pr}/comments", {"body": comment}, method="POST")
    print(f"[actions] 已发布：status={state} context={ctx}，评论见 PR #{args.pr}")
    if blocking:
        sys.exit(2)  # CI 感知阻断


if __name__ == "__main__":
    main()
