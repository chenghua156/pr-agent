#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Q3 爆炸半径检测 —— 大仓小改的专项防线（方案 §9 Q3）。

小 diff 也可能有大爆炸半径：对 diff 中被修改/新增的符号做全仓反向调用统计，
调用方数量超阈值、或命中关键路径 glob 时输出升级信号（触发 Stage 5 对抗深度审查）。

实现：纯 grep 式扫描（升级路径：universal-ctags / 代码图谱 RAG，见 README）。

用法：
  python3 blast_radius.py --diff pr.diff --repo . [--config config/pipeline.json] [--strict]
  --strict 时升级信号导致退出码 2（CI 用）；默认仅输出 JSON 供后续步骤消费
依赖：仅标准库
"""
import argparse
import fnmatch
import json
import os
import re
import sys

# 各语言函数/类定义的启发式（够用即可；ctags 升级路径见 README）
DEF_RES = [
    re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),            # python
    re.compile(r"^\+\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),       # go
    re.compile(r"^\+\s*(?:public|private|protected|static|\s)*[\w<>\[\]]+\s+([A-Za-z_]\w*)\s*\([^;]*$"),  # java/c++
    re.compile(r"^\+\s*function\s+([A-Za-z_$][\w$]*)"),                # js/ts
    re.compile(r"^\+\s*class\s+([A-Za-z_]\w*)"),                       # class
]
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv"}
CODE_EXT = {".py", ".go", ".java", ".c", ".h", ".cpp", ".cc", ".hpp", ".js", ".ts", ".tsx", ".jsx", ".rs", ".rb", ".php", ".cs", ".kt", ".sh"}
# 内建/常用函数名不作为"被定义符号"（实测 `with open(...)` 会被 Java/C++ 启发式误捕）
BUILTIN_SKIP = {"open", "print", "len", "str", "int", "float", "dict", "list", "set", "tuple",
                "copy", "deepcopy", "close", "get", "error", "info", "warning", "debug"}


def changed_files_and_symbols(diff_path):
    files, symbols = [], set()
    cur_file = None
    with open(diff_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"^\+\+\+ b/(.+)$", line.rstrip("\n"))
            if m:
                cur_file = m.group(1)
                if cur_file not in files:
                    files.append(cur_file)
                continue
            if cur_file and line.startswith("+") and not line.startswith("+++"):
                for rx in DEF_RES:
                    sm = rx.match(line)
                    if sm and sm.group(1) not in BUILTIN_SKIP:
                        symbols.add(sm.group(1))
    return files, symbols


def count_callers(repo, symbols, exclude_files):
    """统计每个符号在全仓（排除变更文件自身）被引用的文件数与次数。"""
    result = {}
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if os.path.splitext(name)[1] not in CODE_EXT:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, repo).replace(os.sep, "/")
            if rel in exclude_files:
                continue
            try:
                content = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for sym in symbols:
                n = len(re.findall(r"\b" + re.escape(sym) + r"\s*\(", content))
                if n:
                    r = result.setdefault(sym, {"files": 0, "occurrences": 0})
                    r["files"] += 1
                    r["occurrences"] += n
    return result


def hit_critical(files, globs):
    return [g for g in globs for f in files if fnmatch.fnmatch(f, g)]


def main():
    ap = argparse.ArgumentParser(description="Q3 爆炸半径检测")
    ap.add_argument("--diff", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--strict", action="store_true", help="升级信号时退出码 2")
    args = ap.parse_args()

    cfg = {"caller_threshold": 20, "critical_globs": [],
           "escalate_on_critical_path": True}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg.update(json.load(f).get("blast_radius") or {})

    files, symbols = changed_files_and_symbols(args.diff)
    callers = count_callers(args.repo, symbols, set(files)) if symbols else {}
    critical_hits = hit_critical(files, cfg.get("critical_globs", []))

    reasons = []
    hot = {s: c for s, c in callers.items() if c["files"] >= cfg["caller_threshold"]}
    if hot:
        reasons.append(f"符号调用方文件数超阈值(>{cfg['caller_threshold']}): "
                       + ", ".join(f"{s}({c['files']}个文件/{c['occurrences']}次)" for s, c in sorted(hot.items(), key=lambda kv: -kv[1]["files"])))
    if critical_hits and cfg.get("escalate_on_critical_path", True):
        reasons.append(f"命中关键路径: {', '.join(critical_hits)}")

    out = {
        "escalate": bool(reasons), "reasons": reasons,
        "changed_files": files, "changed_symbols": sorted(symbols),
        "callers": callers, "critical_paths_hit": critical_hits,
        "decision": "升级 Stage 5 对抗深度审查" if reasons else "常规流程",
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.strict and reasons:
        sys.exit(2)


if __name__ == "__main__":
    main()
