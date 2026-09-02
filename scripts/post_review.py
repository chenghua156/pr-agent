#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 4 分级动作 —— Cloudflare 模式：干净放行、critical 阻断、其余蒸馏呈现。

消费 triage.py 输出的 triaged.json：
  - critical（且在 block_on 名单）→ commit status = failure（阻断合并）
  - 其余 → commit status = success + 一条蒸馏后的 PR 评论
  - 评论内嵌 👍/👎 提示，反馈经 noise_ledger.py vote 沉淀（Stage 6 闭环）

语言：所有对外文案经 pr_lang.py（--lang / PR_LANG，默认 en；zh 切中文）

用法：
  GH 环境:  python3 post_review.py --triaged triaged.json --repo owner/name --pr 123 --sha <sha> [--lang zh]
  本地:     python3 post_review.py --triaged triaged.json --dry-run [--lang zh]
依赖：仅标准库；gh 模式需要 gh CLI + GH_TOKEN/GITHUB_TOKEN
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_lang import resolve_lang, t  # noqa: E402

EMOJI = {"critical": "🔴", "medium": "🟡", "low": "🟢"}


def render_comment(triaged, lang):
    """蒸馏后的 PR 评论（人眼可见的全部内容就这些）；文案语言由 lang 决定。"""
    lines = [t("comment.title", lang), "", f"> {triaged.get('summary', '')}", ""]
    findings = triaged.get("findings", [])
    if not findings:
        lines.append(t("comment.no_findings", lang))
    for f in findings:
        sev = f.get("severity", "low")
        lines += [
            f"{EMOJI.get(sev, '🟢')} **[{sev.upper()}] {f.get('title', '')}**（confidence {f.get('confidence', '?')}）",
            t("comment.location", lang, file=f.get("file", "?"), line=f.get("line", "?")),
            t("comment.evidence", lang, evidence=f.get("evidence", "—")),
            t("comment.suggestion", lang, fix=f.get("fix", "—")),
            "",
        ]
    archived = triaged.get("archived", [])
    if archived:
        lines += ["<details>", f"<summary>{t('comment.archived_summary', lang, n=len(archived))}</summary>", ""]
        lines += [f"- {a.get('title', '?')}（{a.get('reason', '?')}）" for a in archived]
        lines += ["", "</details>"]
    lines += ["---", t("comment.feedback", lang)]
    return "\n".join(lines)


def gh_api(endpoint, payload=None, method=None, lang=None):
    cmd = ["gh", "api", "-X", method or ("POST" if payload else "GET"), endpoint]
    if payload is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else "",
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(t("warn.gh_failed", lang, endpoint=endpoint, err=proc.stderr.strip()), file=sys.stderr)
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
    ap.add_argument("--lang", choices=["en", "zh"], help="输出语言（默认 PR_LANG 环境变量/en）")
    args = ap.parse_args()

    lang = resolve_lang(args.lang)

    with open(args.triaged, encoding="utf-8") as f:
        triaged = json.load(f)
    block_on = ["critical"]
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            block_on = json.load(f).get("actions", {}).get("block_on", block_on)

    blocking = [f for f in triaged.get("findings", []) if f.get("severity") in block_on]
    state = "failure" if blocking else "success"
    comment = render_comment(triaged, lang)

    print(t("console.verdict", lang, state=state, n=len(blocking)))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(comment + "\n")
        print(t("console.report_written", lang, path=args.report))
        return
    if args.dry_run:
        print(t("console.comment_preview", lang) + comment)
        return

    if not (args.repo and args.pr):
        sys.exit(t("console.need_repo", lang))
    ctx = "ai-review/triage"
    if args.sha:
        gh_api(f"repos/{args.repo}/statuses/{args.sha}",
               {"state": state, "context": ctx,
                "description": t("status.description", lang,
                                 blocking=len(blocking), total=len(triaged.get("findings", [])))},
               method="POST", lang=lang)
    gh_api(f"repos/{args.repo}/issues/{args.pr}/comments", {"body": comment}, method="POST", lang=lang)
    print(t("console.published", lang, state=state, ctx=ctx, pr=args.pr))
    if blocking:
        sys.exit(2)  # CI 感知阻断


if __name__ == "__main__":
    main()
