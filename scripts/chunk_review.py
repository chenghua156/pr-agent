#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chunk_review.py —— 分片审查（map-reduce）：超预算的 diff 按文件切片，
每片独立过一遍初审引擎（同一提示词/模型/解析），发现合并为单一 findings.json。

解决的问题：PR-Agent TokenHandler 在 diff 超预算时整文件丢弃（只留提示词内
一行小字），初审变成"部分审查"却呈现为完整结论。分片后每个文件必被某一片
覆盖，等效全量审查。

用法：
  python3 chunk_review.py --diff pr.diff --out out/findings.json \
      [--config config/pipeline.json] [--max-chunk-tokens 12000]
环境：
  PR_AGENT_BIN  初审引擎可执行文件（默认 ../pr-agent/.venv/bin/pr-agent）
  FINDINGS_OUT  引擎单次运行的 findings 产物路径（默认 $PIPELINE_OUT/findings.json）
依赖：仅标准库；引擎侧配置沿用部署（模型/提示词/JSON 直出）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# 分片估算：token ≈ 字符数/4（与主流分词器的英文代码近似一致，保守取 3.5）
CHARS_PER_TOKEN = 3.5


def split_diff_by_file(text):
    """把 unified diff 切成 [(path, section_text)]，section 含该文件全部头与 hunks。"""
    sections, cur_path, cur_lines = [], None, []
    header_re = re.compile(r"^(?:diff --git a/.+ b/(.+)|--- a/(.+?)\t?\n?\+{3} b/(.+))")
    for line in text.splitlines(keepends=True):
        m = re.match(r"^diff --git a/(?:.*?) b/(.+)$", line.rstrip("\n"))
        if m:
            if cur_path is not None:
                sections.append((cur_path, "".join(cur_lines)))
            cur_path, cur_lines = m.group(1).strip(), [line]
            continue
        if cur_path is None and line.startswith("--- "):
            # 无 diff --git 头的裸 unified：--- a/x +++ b/x 起始
            src = line[4:].split("\t")[0].strip()
            cur_path = src[2:] if src.startswith("a/") else src
            cur_lines = [line]
            continue
        if cur_path is not None:
            cur_lines.append(line)
    if cur_path is not None:
        sections.append((cur_path, "".join(cur_lines)))
    return sections


def estimate_tokens(s):
    return max(1, int(len(s) / CHARS_PER_TOKEN))


def build_chunks(sections, max_tokens):
    """按文件装桶：单文件超预算也独立成片（引擎自身会再压缩到 hunk 级）。"""
    chunks, cur, cur_tok = [], [], 0
    for path, sec in sections:
        t = estimate_tokens(sec)
        if cur and cur_tok + t > max_tokens:
            chunks.append(cur)
            cur, cur_tok = [], 0
        cur.append((path, sec, t))
        cur_tok += t
    if cur:
        chunks.append(cur)
    return chunks


def run_engine(diff_path, pr_agent_bin, findings_out, log_path, timeout=420):
    """跑一次初审引擎；成功后把 findings 产物搬到 findings_out 并返回 dict。"""
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run([pr_agent_bin, "--diff-file", diff_path, "review"],
                              stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    src = os.environ.get("FINDINGS_OUT", "")
    if not src:
        # 引擎默认写部署级 findings_output_root/findings.json（相对名 findings.json）
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "out", "findings.json")
    if proc.returncode != 0 or not os.path.exists(src):
        return None
    data = json.load(open(src, encoding="utf-8"))
    shutil.move(src, findings_out)
    return data


def main():
    ap = argparse.ArgumentParser(description="分片审查：超预算 diff 的全量覆盖初审")
    ap.add_argument("--diff", required=True)
    ap.add_argument("--out", required=True, help="合并后的 findings.json")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--max-chunk-tokens", type=int, default=None,
                    help="每片 token 预算（默认读 config.review_chunk_tokens 或 12000）")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pr_agent_bin = os.environ.get("PR_AGENT_BIN", os.path.join(root, "..", "pr-agent", ".venv", "bin", "pr-agent"))
    if not os.path.exists(pr_agent_bin):
        pr_agent_bin = os.path.abspath(os.environ.join(root, "..", "pr-agent", ".venv", "bin", "pr-agent"))

    max_tokens = args.max_chunk_tokens or 12000
    if os.path.exists(args.config):
        cfg = json.load(open(args.config, encoding="utf-8"))
        max_tokens = args.max_chunk_tokens or cfg.get("review", {}).get("chunk_tokens", 12000)

    text = open(args.diff, encoding="utf-8", errors="replace").read()
    sections = split_diff_by_file(text)
    chunks = build_chunks(sections, max_tokens)
    print(f"[chunk-review] {len(sections)} 文件 / 估算 {estimate_tokens(text)} tokens "
          f"→ {len(chunks)} 片（每片 ≤{max_tokens} tokens）")

    merged, reviewed_all = [], []
    tmpdir = tempfile.mkdtemp(prefix="chunkrev-")
    for i, chunk in enumerate(chunks, 1):
        chunk_path = os.path.join(tmpdir, f"chunk{i}.diff")
        with open(chunk_path, "w", encoding="utf-8") as f:
            f.write("".join(sec for _, sec, _ in chunk))
        paths = [p for p, _, _ in chunk]
        print(f"[chunk-review] 片 {i}/{len(chunks)}：{len(paths)} 文件 "
              f"({', '.join(os.path.basename(p) for p in paths[:3])}{'…' if len(paths) > 3 else ''})")
        data = run_engine(chunk_path, pr_agent_bin,
                          os.path.join(tmpdir, f"findings{i}.json"),
                          os.path.join(tmpdir, f"log{i}.txt"))
        if data is None:
            print(f"[chunk-review]   ⚠️ 片 {i} 引擎失败（该文件集未覆盖，见 log）", file=sys.stderr)
            continue
        fs = data.get("findings", [])
        for j, f in enumerate(fs, 1):
            f["id"] = f"C{i}S{j}"
            f["chunk"] = i
        merged.extend(fs)
        reviewed_all.extend(paths)
        print(f"[chunk-review]   发现 {len(fs)} 条")

    all_paths = [p for p, _ in sections]
    envelope = {
        "source": "pr-agent-chunked",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run": {"pid": os.getpid(), "host": os.uname().nodename, "chunks": len(chunks)},
        "findings": merged,
        "review_coverage": {
            "known": True,
            "reviewed_files": reviewed_all,
            "dropped_files": [p for p in all_paths if p not in reviewed_all],
            "chunked": True,
        },
    }
    if envelope["review_coverage"]["dropped_files"]:
        n = len(envelope["review_coverage"]["dropped_files"])
        envelope["coverage_warning"] = (
            f"{n} file(s) failed chunk review (engine error) and were NOT reviewed")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(envelope, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[chunk-review] 合并 {len(merged)} 条发现 → {args.out}；"
          f"覆盖 {len(reviewed_all)}/{len(all_paths)} 文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
