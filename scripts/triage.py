#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 3 分诊降噪层 —— 本方案的灵魂。

输入：Stage 2 初审的发现列表（高召回、含噪音）
处理：去重 / 剔除lint重复 / 对照噪音台账剔误报 / 核验证据 / 重定级 / 截断到 max_findings
输出：triaged.json（Stage 4 分级动作的输入）

两种发现来源：
  1) --findings findings.json   （文件模式，本地或 CI）
  2) --pr https://github.com/o/r/pull/1  （GitHub 模式，用 gh 拉取 bot 评论并解析 [AI-FINDING] 块）

两种运行模式：
  --dry-run   规则分诊（无 API 依赖，CI 降级模式 / 本地测试）
  默认        LLM 分诊（调用 config/pipeline.json 里配置的异构模型）

依赖：仅 Python3 标准库；LLM 模式需要环境变量 TRIAGE_API_KEY
用法示例见 README.md
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

DEFAULT_CONFIG = {
    "models": {"triage_model": "deepseek-chat",
               "triage_base_url": "https://api.deepseek.com/v1"},
    "triage": {"max_findings": 5, "min_confidence": 0.6,
               "prompt_template": "config/triage_prompt.txt"},
}

FINDING_RE = re.compile(
    r"\[AI-FINDING\]\s*severity=(\w+)\s+confidence=([\d.]+)\s+file=([^\s:]+):(\d+)\s*\n"
    r"title:\s*(.+?)\s*\n+"
    r"evidence:\s*(.+?)\s*\n+"
    r"fix:\s*(.+?)\s*(?=\n\[AI-FINDING\]|\n\S|\Z)",
    re.S)

SEV_ORDER = {"critical": 0, "medium": 1, "low": 2}


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    user = load_json(path)
    if user:
        for section in ("models", "triage"):
            cfg[section].update(user.get(section) or {})
        cfg.update({k: v for k, v in user.items() if k not in cfg})
    return cfg


# ---------------------------------------------------------------- 发现获取
def fetch_pr_comments(pr_url):
    """用 gh CLI 拉取 PR 的 review comments + issue comments（全部文本）。"""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        sys.exit("错误：--pr 需要形如 https://github.com/owner/repo/pull/123 的 URL")
    owner, repo, num = m.groups()
    endpoints = [
        f"repos/{owner}/{repo}/pulls/{num}/comments",
        f"repos/{owner}/{repo}/issues/{num}/comments",
    ]
    texts = []
    for ep in endpoints:
        try:
            out = subprocess.run(["gh", "api", "--paginate", ep],
                                 capture_output=True, text=True, check=True)
            for c in json.loads(out.stdout or "[]"):
                texts.append((c.get("user", {}).get("login", "?"),
                              c.get("body", "") or ""))
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"警告：gh api {ep} 失败：{e}", file=sys.stderr)
    return texts


def parse_findings_from_comments(comments):
    """从 bot 评论中解析 [AI-FINDING] 结构块（.pr_agent.toml 强制的格式）。"""
    findings, seq = [], 1
    for author, body in comments:
        for sev, conf, path, line, title, evidence, fix in FINDING_RE.findall(body):
            findings.append({
                "id": f"S{seq}", "severity": sev.lower(),
                "confidence": max(0.0, min(1.0, float(conf))),
                "file": path, "line": int(line),
                "title": title.strip(), "evidence": evidence.strip(),
                "fix": fix.strip(), "source": author,
            })
            seq += 1
    return findings


# ---------------------------------------------------------------- 规则分诊（dry-run / 降级模式）
def ledger_hit(finding, ledger):
    """命中噪音台账：文本关键词交集匹配（台账 match 字段全部出现在发现文本中）。"""
    text = " ".join([finding.get("title", ""), finding.get("evidence", ""),
                     finding.get("file", "")]).lower()
    for p in ledger.get("patterns", []):
        keys = [k.lower() for k in p.get("match", [])]
        if keys and all(k in text for k in keys):
            return p.get("id", "?")
    return None


def rule_based_triage(findings, ledger, cfg):
    """无 LLM 的降级分诊：去重 → 台账剔除 → 严重度排序 → 截断。"""
    tcfg = cfg["triage"]
    kept, archived, seen = [], [], set()
    for f in sorted(findings, key=lambda x: (SEV_ORDER.get(x.get("severity", "low"), 3),
                                              -float(x.get("confidence", 0.5)))):
        nid = ledger_hit(f, ledger)
        if nid:
            archived.append({"title": f.get("title", "?"), "reason": f"noise_ledger:{nid}"})
            continue
        sig = (f.get("file", ""), f.get("line", 0), f.get("title", "").lower()[:40])
        if sig in seen:
            archived.append({"title": f.get("title", "?"), "reason": "duplicate"})
            continue
        seen.add(sig)
        if float(f.get("confidence", 0.5)) < tcfg["min_confidence"]:
            archived.append({"title": f.get("title", "?"), "reason": "low_confidence"})
            continue
        kept.append(f)
        if len(kept) >= tcfg["max_findings"]:
            break
    archived.extend([{"title": f["title"], "reason": "capped"} for f in findings[len(kept) + len(archived):]])
    return {"mode": "rules", "findings": kept, "archived": archived,
            "summary": f"规则分诊：{len(findings)} 条初审发现 -> {len(kept)} 条保留"}


# ---------------------------------------------------------------- LLM 分诊
def llm_triage(findings, ledger, diff_text, cfg):
    tpl_path = cfg["triage"]["prompt_template"]
    tpl = open(tpl_path, encoding="utf-8").read() if os.path.exists(tpl_path) else ""
    if not tpl:
        sys.exit(f"错误：分诊 prompt 模板不存在：{tpl_path}")
    ledger_brief = json.dumps(
        [{"module": p.get("module"), "pattern": p.get("pattern"),
          "match": p.get("match")} for p in ledger.get("patterns", [])],
        ensure_ascii=False)
    prompt = (tpl.replace("{MAX_FINDINGS}", str(cfg["triage"]["max_findings"]))
                 .replace("{MIN_CONFIDENCE}", str(cfg["triage"]["min_confidence"]))
                 .replace("{NOISE_LEDGER}", ledger_brief or "（空）")
                 .replace("{FINDINGS}", json.dumps(findings, ensure_ascii=False, indent=1))
                 .replace("{DIFF}", (diff_text or "（未提供）")[:60000]))
    backend = cfg["models"].get("triage_backend", "openai")
    if backend == "dsh":
        return {"mode": "dsh", **dsh_triage(prompt, cfg)}
    return {"mode": "llm", **call_llm(prompt, cfg)}


def extract_json(text):
    """从模型输出中稳健提取 JSON：容忍代码围栏与前置/后置叙述文字。

    dsh headless 是带工具的 agent——它会先写核验过程再给结论，
    最终文本可能是"叙述 + JSON"混合体（实测如此）。
    """
    text = text.strip()
    for candidate in (text, re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def dsh_triage(prompt, cfg):
    """dsh 后端：经 `dsh --profile headless <task>` 复用 DSH 已配置的模型与凭据。

    无需 TRIAGE_API_KEY——模型路由、密钥、provider 全部由 DSH 侧管理。
    dsh_command 可覆写（测试时指向桩脚本）。
    """
    import shlex
    cmd = shlex.split(cfg["models"].get("dsh_command", "dsh"))
    proc = subprocess.run(cmd + ["--profile", "headless", prompt],
                          capture_output=True, text=True, timeout=900)
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        sys.exit(f"错误：dsh headless 分诊失败（exit={proc.returncode}）："
                 f"{(proc.stderr or '(空)')[:500]}")
    parsed = extract_json(text)
    if parsed is None:
        sys.exit(f"错误：dsh 分诊返回中找不到 JSON：\n{text[:500]}")
    for k in ("findings", "archived", "summary"):
        parsed.setdefault(k, [] if k != "summary" else "")
    return parsed


def call_llm(prompt, cfg):
    base = cfg["models"]["triage_base_url"].rstrip("/")
    model = cfg["models"]["triage_model"]
    key = os.environ.get("TRIAGE_API_KEY")
    if not key:
        sys.exit("错误：LLM 分诊需要环境变量 TRIAGE_API_KEY（或使用 --dry-run）")
    body = json.dumps({"model": model, "temperature": 0.1,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode())
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)  # 剥代码围栏
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        sys.exit(f"错误：分诊模型返回非 JSON：\n{text[:500]}")
    for k in ("findings", "archived", "summary"):
        parsed.setdefault(k, [] if k != "summary" else "")
    return parsed


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Stage 3 分诊降噪层")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--ledger", default="config/noise_ledger.json")
    ap.add_argument("--findings", help="发现列表 JSON 文件（文件模式）")
    ap.add_argument("--pr", help="PR URL，从 bot 评论解析发现（GitHub 模式）")
    ap.add_argument("--diff", help="diff 文件（LLM 模式验证据用）")
    ap.add_argument("--out", default="triaged.json")
    ap.add_argument("--dry-run", action="store_true", help="规则分诊，不调 LLM")
    args = ap.parse_args()

    cfg = load_config(args.config)
    ledger = load_json(args.ledger, {"patterns": [], "votes": []})

    if args.findings:
        raw = load_json(args.findings, [])
        findings = raw.get("findings", []) if isinstance(raw, dict) else raw
        findings = [f for f in findings if isinstance(f, dict)]
    elif args.pr:
        findings = parse_findings_from_comments(fetch_pr_comments(args.pr))
    else:
        sys.exit("错误：需要 --findings 或 --pr 之一作为发现来源")
    print(f"[triage] 初审发现 {len(findings)} 条（噪音台账 {len(ledger.get('patterns', []))} 条模式）")

    if not findings:
        result = {"mode": "empty", "findings": [], "archived": [],
                  "summary": "初审无发现"}
    elif args.dry_run:
        result = rule_based_triage(findings, ledger, cfg)
    else:
        diff_text = open(args.diff, encoding="utf-8").read() if args.diff else ""
        result = llm_triage(findings, ledger, diff_text, cfg)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    kept = result.get("findings", [])
    sev = {s: sum(1 for x in kept if x.get("severity") == s) for s in SEV_ORDER}
    print(f"[triage] 模式={result['mode']} 保留 {len(kept)} 条 "
          f"(critical={sev['critical']} medium={sev['medium']} low={sev['low']}) "
          f"归档 {len(result.get('archived', []))} 条 -> {args.out}")


if __name__ == "__main__":
    main()
