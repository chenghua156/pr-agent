#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 0 规模门禁 —— 固化的单一事实源实现。

此前状态：CI 版逻辑硬编码在 pr-gate.yml（用 git numstat，计数正确）；
本地 pipeline.sh 无此步；演示时用 grep 模拟曾漏数"光杆+"行（新增空行）。
本脚本统一两个入口，计数规则与 git diff --numstat 对齐。

计数口径：变更行 = 新增行(含空行) + 删除行；上下文/hunk头/文件头不计入
（上下文的预算职责在 Stage 2 的 TokenHandler，见设计文档 §2）。

用法：
  python3 size_gate.py --diff pr.diff [--config config/pipeline.json] [--label bulk-change]
  python3 size_gate.py --numstat <file>          # 直接读 git numstat 输出（CI 模式）
退出码：0 通过 / 2 超限（CI 直接 fail）/ 3 豁免（跳过 AI 审查，仅提示）
"""
import argparse
import json
import os
import sys

DEFAULTS = {"max_diff_lines": 400, "exempt_label": "bulk-change"}


def load_cfg(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update((json.load(f).get("stage0") or {}))
    return cfg


def count_diff_file(path):
    """按 unified-diff 语义逐行分类计数（与 git numstat 对齐）。"""
    added = removed = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("+++") or line.startswith("---") or line.startswith("@@") \
                    or line.startswith("\\") or line.startswith("diff --git") or line.startswith("index "):
                continue
            if line.startswith("+"):
                added += 1          # 光杆 "+"（新增空行）也计入
            elif line.startswith("-"):
                removed += 1
            # 空格开头=上下文 / 空行=GNU diff 的空白上下文 → 不计
    return added, removed


def count_numstat(path):
    """读 `git diff --numstat` 输出：两列数字，- 表示二进制。"""
    added = removed = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0] != "-":
                added += int(parts[0])
                removed += int(parts[1])
    return added, removed


def main():
    ap = argparse.ArgumentParser(description="Stage 0 规模门禁")
    ap.add_argument("--diff", help="unified diff 文件")
    ap.add_argument("--numstat", help="git diff --numstat 输出文件（CI 模式）")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--label", help="PR 当前标签（逗号分隔），命中豁免标签则走简化通道")
    args = ap.parse_args()

    if not args.diff and not args.numstat:
        sys.exit("错误：需要 --diff 或 --numstat 之一")
    cfg = load_cfg(args.config)

    if args.numstat:
        added, removed = count_numstat(args.numstat)
    else:
        added, removed = count_diff_file(args.diff)
    total = added + removed

    labels = {s.strip() for s in (args.label or "").split(",") if s.strip()}
    exempt = cfg["exempt_label"] in labels

    print(f"[size-gate] 新增 {added} + 删除 {removed} = 变更 {total} 行"
          f"（阈值 {cfg['max_diff_lines']}，上下文行不计入——其预算归 Stage 2 TokenHandler）")
    if exempt:
        print(f"[size-gate] 🛂 豁免通道（{cfg['exempt_label']}）：跳过 AI 审查，仅执行确定性校验")
        sys.exit(3)
    if total > cfg["max_diff_lines"]:
        print(f"[size-gate] ❌ 超限：{total} > {cfg['max_diff_lines']} —— 请拆分为 stacked PR；"
              f"机械性大改可打 '{cfg['exempt_label']}' 标签走豁免")
        sys.exit(2)
    print(f"[size-gate] ✅ 通过（{total} ≤ {cfg['max_diff_lines']}），放行进入 Stage 1")
    sys.exit(0)


if __name__ == "__main__":
    main()
