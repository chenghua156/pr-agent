#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pr_lang.py —— PR 内容语言统一模块（默认英文，可切中文）

流水线所有"会提交到 PR / 展示给评审人"的自有文案统一经此模块输出：
  - post_review.py 的蒸馏评论 / 控制台消息 / status 描述
  - test_selector.py 的选型报告（独立组件内建双语目录，不依赖本文件）

语言决议顺序：显式 --lang 参数 > 环境变量 PR_LANG > 默认 en
合法值：en / zh（zh-CN、cn、ZH 等别名归一化为 zh）

CI/GitHub 用法：
  - workflow_dispatch 输入 lang（en/zh，默认 en）
  - 或仓库变量 PR_LANG（push/PR 事件的统一开关）
Jenkins 用法：LANG_MODE 构建参数 → 导出 PR_LANG
"""
import os

DEFAULT_LANG = "en"
ALIASES = {"en": "en", "english": "en", "zh": "zh", "cn": "zh", "chinese": "zh",
           "zh-cn": "zh", "zh_cn": "zh", "zh-hans": "zh"}

# ---------------------------------------------------------------- 消息目录
STRINGS = {
    "en": {
        # post_review 评论
        "comment.title": "## 🤖 AI Review (Triaged)",
        "comment.no_findings": "✅ **AI: no blocking findings** — no blocking issues detected; "
                               "reviewers should focus on design and business correctness.",
        "comment.location": "- Location: `{file}:{line}`",
        "comment.evidence": "- Evidence: {evidence}",
        "comment.suggestion": "- Suggestion: {fix}",
        "comment.archived_summary": "Archived {n} findings (dedup/ledger/insufficient evidence — expand to verify)",
        "comment.feedback": "*Feedback loop: react 👍 if a finding is useful, 👎 if it is a false positive "
                            "(reactions on this comment). Weekly distilled into triage rules via "
                            "`noise_ledger.py add`.*",
        # post_review 控制台 / status
        "console.verdict": "[actions] Verdict: {state} ({n} critical blocking)",
        "console.report_written": "[actions] Report written: {path}",
        "console.comment_preview": "\n----- PR comment to be published -----\n",
        "console.published": "[actions] Published: status={state} context={ctx}, comment on PR #{pr}",
        "console.need_repo": "Error: gh mode requires --repo owner/name --pr N [--sha]",
        "status.description": "{blocking} blocking / {total} findings",
        "warn.gh_failed": "Warning: gh api {endpoint} failed: {err}",
    },
    "zh": {
        "comment.title": "## 🤖 AI 审查（分诊后）",
        "comment.no_findings": "✅ **AI: 无阻断性发现** —— 未发现阻断性问题，请 reviewer 关注设计与业务正确性。",
        "comment.location": "- 位置：`{file}:{line}`",
        "comment.evidence": "- 证据：{evidence}",
        "comment.suggestion": "- 建议：{fix}",
        "comment.archived_summary": "已归档 {n} 条（去重/台账/证据不足，可展开核查）",
        "comment.feedback": "*反馈闭环：对以上每条发现，有用点 👍 / 误报点 👎（本条评论的 reactions），"
                            "每周经 `noise_ledger.py add` 沉淀为分诊规则。*",
        "console.verdict": "[actions] 分级结果：{state}（阻断 {n} 条 critical）",
        "console.report_written": "[actions] 报告已写入：{path}",
        "console.comment_preview": "\n----- 将发布的 PR 评论 -----\n",
        "console.published": "[actions] 已发布：status={state} context={ctx}，评论见 PR #{pr}",
        "console.need_repo": "错误：gh 模式需要 --repo owner/name --pr N [--sha]",
        "status.description": "{blocking} 条阻断 / 共 {total} 条发现",
        "warn.gh_failed": "警告：gh api {endpoint} 失败：{err}",
    },
}


def resolve_lang(explicit=None):
    """显式参数 > PR_LANG 环境变量 > 默认 en；非法值回退 en 并告警。"""
    raw = (explicit or os.environ.get("PR_LANG") or DEFAULT_LANG).strip().lower()
    lang = ALIASES.get(raw)
    if lang is None:
        print("pr_lang: unknown lang %r, fallback to %r" % (raw, DEFAULT_LANG), flush=True)
        lang = DEFAULT_LANG
    return lang


def t(key, lang=None, **fmt):
    """取指定语言的文案；缺失键回退英文再回退键名。"""
    lg = resolve_lang(lang)
    s = STRINGS.get(lg, {}).get(key) or STRINGS[DEFAULT_LANG].get(key) or key
    return s.format(**fmt) if fmt else s


if __name__ == "__main__":
    # 自检：python3 pr_lang.py [en|zh]
    import sys as _sys
    lg = _sys.argv[1] if len(_sys.argv) > 1 else None
    print("lang =", resolve_lang(lg))
    for k in ("comment.title", "comment.no_findings", "console.verdict"):
        print("%s -> %s" % (k, t(k, lg)))
