#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_selector.py —— 变更驱动的测试类型选择器（组件版 Phase 1）

定位：Test Impact Analysis（TIA）/ 风险驱动测试选择的轻量确定性实现。
输入 diff，输出本次变更「必须测哪些类型 + 条件选测 + 回归范围」的机器可解析
清单（testtypes.json），供用例生成 skill 链（dp-project-testcase →
testpoint-completion → auto-testcase-script）与 CI 编排消费。

只做选型，不做执行；只输出建议，不做门禁（exit 0 = 正常出报告）。

三步判定法（与 README 的映射表一一对应）：
  Step1 层级规则    —— 按文件路径判改动层级（UI/API/逻辑/DB/配置/依赖/测试）
  Step2 敏感性规则  —— 按新增行内容判性质（安全/并发/资金事务/性能热路径）
  Step3 联动信号    —— blast_radius.escalate=true → 回归范围 module→chain
                       --bugfix → 追加 复现用例+回归

用法：
  python3 test_selector.py --diff pr.diff
  python3 test_selector.py --diff pr.diff --bugfix --out testtypes.json
  python3 test_selector.py --diff pr.diff --blast-radius blast_radius.json
  python3 test_selector.py --dump-rules > rules.json   # 导出内置规则供编辑

依赖：仅 Python3 标准库。
"""
import argparse
import fnmatch
import json
import re
import sys

# ---------------------------------------------------------------- 双语目录（默认 en，PR_LANG/--lang 切换）
LANG = {
    "en": {
        "types": {
            "ui_functional": "UI Functional", "unit": "Unit", "component": "Component", "e2e": "E2E",
            "smoke": "Smoke", "regression": "Regression", "functional": "Functional",
            "integration": "Integration", "contract": "Contract", "consumer_contract": "Consumer Contract",
            "version_compat": "Version Compat", "visual_regression": "Visual Regression",
            "compatibility": "Compatibility", "responsive": "Responsive", "i18n": "i18n",
            "session": "Session", "security": "Security", "fuzz": "Fuzz",
            "data_integrity": "Data Integrity", "idempotency": "Idempotency",
            "performance_load": "Perf-Load", "performance_stress": "Perf-Stress",
            "performance_spike": "Perf-Spike", "performance_soak": "Perf-Soak",
            "capacity": "Capacity", "frontend_perf": "Frontend Perf", "reliability_chaos": "Reliability/Chaos",
            "reproduce_case": "Reproduce Case (→ regression asset)",
        },
        "report_title": "Test Type Selection Report (must %d / conditional %d / regression scope %s)",
        "must": "MUST-TEST TYPES", "cond": "CONDITIONAL (decide by condition; agent-readable)",
        "risk": "RISK LEVEL", "scope": "REGRESSION SCOPE", "reqs": "LINKED REQUIREMENTS (traceability③)",
        "cases": "SELECTED REGRESSION CASES (classic TIA⑤)", "layers": "LAYERS TOUCHED",
        "signals": "SIGNALS", "stats": "STATS",
        "when": "when", "from": "from", "evidence": "evidence",
        "path_match": "path", "type_match": "type",
        "tia_path_detail": "change hits case coverage path (classic TIA selected)",
        "tia_type_detail": "case types hit must-list: ",
        "risk_fmt": "%s (score %d, %s)", "high": "High", "medium": "Medium", "low": "Low",
        "stats_fmt": "files %d / added %d / removed %d",
        "scope_default": "Default: module-level regression",
        "scope_br": "blast_radius.escalate=true (dependency analysis) → chain-level regression + adversarial exploration",
        "scope_risk": "risk score %d ≥ high threshold (%s) → chain-level regression",
        "bugfix_evidence": "--bugfix: reproduce case must go red→green first",
        "written_to": "Result written to %s",
        "warn_bad_trace": "Warning: failed to load trace (%s), skipping traceability/case selection",
        "warn_bad_br": "Warning: failed to load blast-radius (%s), ignoring linkage signal",
        "err_read_diff": "Failed to read diff: %s", "err_no_files": "No file changes parsed from diff",
    },
    "zh": {
        "types": {
            "ui_functional": "UI功能", "unit": "单元", "component": "组件", "e2e": "端到端E2E",
            "smoke": "冒烟", "regression": "回归", "functional": "功能", "integration": "集成",
            "contract": "契约", "consumer_contract": "消费者契约", "version_compat": "版本兼容",
            "visual_regression": "视觉回归", "compatibility": "兼容性", "responsive": "响应式",
            "i18n": "国际化", "session": "会话/登录态", "security": "安全", "fuzz": "模糊测试",
            "data_integrity": "数据完整性", "idempotency": "幂等性",
            "performance_load": "性能-负载", "performance_stress": "性能-压力",
            "performance_spike": "性能-尖峰", "performance_soak": "性能-浸泡/长稳",
            "capacity": "容量", "frontend_perf": "前端性能", "reliability_chaos": "可靠性/混沌",
            "reproduce_case": "复现用例(转回归资产)",
        },
        "report_title": "测试类型选择报告（必测 %d 类 / 条件选测 %d 类 / 回归范围 %s）",
        "must": "必测类型", "cond": "条件选测（按 condition 成立与否取舍，可交 agent 判读）",
        "risk": "风险分级", "scope": "回归范围", "reqs": "关联需求（追溯③）",
        "cases": "选中回归用例（经典TIA⑤）", "layers": "改动层级",
        "signals": "触发信号", "stats": "改动统计",
        "when": "当", "from": "来自", "evidence": "证据",
        "path_match": "path匹配", "type_match": "type匹配",
        "tia_path_detail": "改动命中用例覆盖路径（经典TIA选中）",
        "tia_type_detail": "用例类型命中必测: ",
        "risk_fmt": "%s（分值 %d，%s）", "high": "高风险", "medium": "中风险", "low": "低风险",
        "stats_fmt": "文件 %d / 新增 %d 行 / 删除 %d 行",
        "scope_default": "默认：模块级回归",
        "scope_br": "blast_radius.escalate=true（依赖分析）→ 升级链路级回归 + 对抗性探索",
        "scope_risk": "风险分 %d ≥ 高阈值（%s）→ 升级链路级回归",
        "bugfix_evidence": "--bugfix 指定：复现用例须先红后绿",
        "written_to": "结果已写入 %s",
        "warn_bad_trace": "警告：trace 读取失败(%s)，忽略追溯/用例选择",
        "warn_bad_br": "警告：blast_radius 读取失败(%s)，忽略联动信号",
        "err_read_diff": "读 diff 失败: %s", "err_no_files": "diff 中未解析到任何文件改动",
    },
}
DEFAULT_LANG = "en"


def resolve_lang(explicit=None):
    """--lang > PR_LANG 环境变量 > 默认 en（本组件独立自包含，不依赖 pr_lang.py）。"""
    import os
    raw = (explicit or os.environ.get("PR_LANG") or DEFAULT_LANG).strip().lower()
    if raw in ("zh", "cn", "zh-cn", "zh_cn", "chinese", "zh-hans"):
        return "zh"
    if raw != "en" and raw:
        print("test_selector: unknown lang %r, fallback to 'en'" % raw, flush=True)
    return "en"

# ---------------------------------------------------------------- 内置规则（--rules 可外部覆盖）
DEFAULT_RULES = {
    # Step 1：层级规则（路径命中即触发；paths 取小写子串，globs 取 fnmatch）
    "layers": [
        {"id": "frontend_ui",
         "globs": ["*.vue", "*.tsx", "*.jsx", "*.svelte", "*.html", "*.css", "*.scss", "*.less"],
         "paths": ["src/components/", "ui/", "web/src/", "static/"],
         "must": ["ui_functional", "visual_regression", "smoke"],
         "conditional": [
             {"type": "compatibility", "condition": "多浏览器发布矩阵"},
             {"type": "responsive", "condition": "布局/断点改动"},
             {"type": "i18n", "condition": "文案或多语言键改动"}]},
        {"id": "frontend_logic",
         "globs": ["*.ts", "*.js"],
         "paths": ["store/", "composables/", "hooks/", "src/pages/", "src/api/"],
         "must": ["unit", "component", "e2e"],
         "conditional": [{"type": "session", "condition": "涉及登录态/Cookie/Token"}]},
        {"id": "api_surface",
         "globs": [],
         "paths": ["api/", "controller", "routes", "handlers/", "resource", "views.py", "urls.py", "servlet"],
         "must": ["contract", "functional", "regression"],
         "conditional": [
             {"type": "version_compat", "condition": "删改了对外字段或参数"},
             {"type": "consumer_contract", "condition": "有已知下游消费方（Pact）"}]},
        {"id": "backend_logic",
         "globs": [],
         "paths": ["service", "biz/", "core/", "logic/", "domain/", "modules/"],
         "must": ["functional", "integration", "regression"],
         "conditional": [
             {"type": "idempotency", "condition": "订单/资金/重试语义"},
             {"type": "data_integrity", "condition": "写库或事务"}]},
        {"id": "db_schema",
         "globs": ["*.sql"],
         "paths": ["migration", "migrations/", "dao/", "models/"],
         "must": ["data_integrity", "integration", "regression"],
         "conditional": [
             {"type": "capacity", "condition": "大表结构变更"},
             {"type": "performance_load", "condition": "索引或慢查询调整"}]},
        {"id": "config",
         "globs": ["*.yaml", "*.yml", "*.ini", "*.toml", "*.env", "*.conf", "*.cfg"],
         "paths": ["config/", "etc/"],
         "must": ["smoke", "regression"],
         "conditional": [{"type": "performance_spike", "condition": "限流/配额值调整"}]},
        {"id": "dependency",
         "globs": ["package.json", "requirements.txt", "go.mod", "pom.xml", "Cargo.toml", "Makefile"],
         "paths": [],
         "must": ["smoke", "regression"],
         "conditional": [{"type": "contract", "condition": "对外 API 面大"}]},
        {"id": "test_code",
         "globs": ["test_*.py", "*_test.go", "*.test.js", "*.spec.ts"],
         "paths": ["test", "tests/", "spec/", "__tests__"],
         "must": ["unit"],
         "conditional": []},
    ],
    # Step 2：敏感性规则（对新增行内容做 re.search；flags 里含 i 则忽略大小写）
    "sensitivities": [
        {"id": "hardcoded_secret",
         "regex": [r"(?:sk-[A-Za-z0-9]|AKIA|BEGIN (?:RSA )?PRIVATE KEY)",
                   r"(?:password|passwd|pass|pwd|secret|token|api_?key|access_?key)\s*[=:]\s*['\"][^'\"]{8,}"],
         "flags": "i", "must": ["security"], "conditional": []},
        {"id": "auth_surface",
         "regex": [r"\b(?:auth|login|logout|password|token|jwt|session|cookie|encrypt|decrypt|hmac|signature|permission|privilege)\w*\b"],
         "flags": "i", "must": ["security"],
         "conditional": [{"type": "fuzz", "condition": "外部输入面大"}]},
        {"id": "sql_concat",
         "regex": [r"f['\"].*(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)",
                   r"execute\s*\(\s*['\"~]?(?:SELECT|INSERT|UPDATE|DELETE).*(?:\+|%s\s*\)|\.format|\{)"],
         "flags": "i", "must": ["security", "fuzz"], "conditional": []},
        {"id": "shell_exec",
         "regex": [r"\bsubprocess\b|\bos\.system\b|shell\s*=\s*True|\beval\s*\(|\bexec\s*\("],
         "flags": "", "must": ["security"], "conditional": []},
        {"id": "concurrency",
         "regex": [r"\b(?:Thread|threading|RLock|Mutex|Semaphore|Queue|channel|asyncio|synchronized|atomic)\b|go\s+func"],
         "flags": "", "must": ["integration", "performance_stress"],
         "conditional": [{"type": "performance_spike", "condition": "存在突发流量场景"},
                         {"type": "performance_soak", "condition": "常驻服务"}]},
        {"id": "money_tx",
         "regex": [r"\b(?:charge|payment|refund|order|invoice|balance|transaction|withdraw|deposit|billing)\w*\b"],
         "flags": "i", "must": ["functional", "idempotency", "data_integrity"], "conditional": []},
        {"id": "perf_hotpath",
         "regex": [r"\bfor\s+\w+\s+in\b|\bwhile\b|\.query\(|\bSELECT\b.*\bFROM\b|\bcache\b"],
         "flags": "i", "must": [],
         "conditional": [{"type": "performance_load", "condition": "循环内查询/缓存失效/N+1"}]},
        {"id": "resource_leak",
         "regex": [r"(?<!with )\bopen\s*\("],
         "flags": "", "must": [],
         "conditional": [{"type": "performance_soak", "condition": "句柄未用 with 关闭（长稳暴露泄漏）"}]},
    ],
    # Step 3：联动信号
    "bugfix": {"must_add": ["regression", "reproduce_case"]},
    "regression_scope": {"default": "module", "escalate": "chain"},
    # 风险模型（风险驱动测试选择④）：层级/信号加权 → 风险分 → 分级，高级别升级回归范围
    "risk_model": {
        "layer_weights": {"frontend_ui": 2, "frontend_logic": 2, "api_surface": 3,
                          "backend_logic": 3, "db_schema": 4, "config": 2,
                          "dependency": 3, "test_code": 1},
        "signal_weights": {"sens:hardcoded_secret": 4, "sens:sql_concat": 4, "sens:shell_exec": 3,
                           "sens:auth_surface": 2, "sens:concurrency": 3, "sens:money_tx": 3,
                           "sens:perf_hotpath": 1, "sens:resource_leak": 1,
                           "file:deleted": 3, "flag:bugfix": 1},
        "thresholds": {"medium": 6, "high": 10},
        "level_names": {"low": "低风险", "medium": "中风险", "high": "高风险"}
    },
    # 追溯与用例选择（③追溯 + ⑤经典TIA）：外部 trace.json 提供需求/用例映射
    # trace.json 契约：
    #   {"requirements": [{"id":"REQ-101","paths":["services/payment/*"],"desc":"支付扣款"}],
    #    "cases": [{"id":"TC-001","name":"支付金额边界","paths":["services/payment/pay.py"],
    #               "types":["functional","idempotency"]}]}
    # 匹配：路径命中(fnmatch)优先=经典TIA选中回归用例；类型∩必测 次之=建议人工确认
    "trace": {"path_priority": True, "type_fallback": True},
}

MAX_EVIDENCE_PER_TYPE = 3   # 每个类型最多保留的现场证据条数


# ---------------------------------------------------------------- diff 解析
def parse_diff(text):
    """解析 unified diff（git/svn 均可）→ [file]；file 含 path/added/removed 与真实行号。"""
    files, cur = [], None
    hunk_line = None  # 新文件侧当前行号
    for line in text.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r"^diff --git a/(.*?) b/(.*)$", line)
            cur = {"path": m.group(2).strip() if m else "", "is_new": False,
                   "deleted": False, "added": [], "removed": []}
            files.append(cur); hunk_line = None
        elif line.startswith("--- "):
            src = line[4:].split("\t")[0].strip()
            # svn/裸 unified diff 无 diff --git 头：上一文件已收集到增删行时，此处开新文件段
            if cur is not None and (cur["added"] or cur["removed"]):
                cur = {"path": "", "is_new": False, "deleted": False, "added": [], "removed": []}
                files.append(cur)
                hunk_line = None
            if cur is None:
                cur = {"path": "", "is_new": False, "deleted": False, "added": [], "removed": []}
                files.append(cur)
                hunk_line = None
            if src == "/dev/null":
                cur["is_new"] = True
            else:
                cur["path"] = re.sub(r"^[ab]/", "", src)
        elif line.startswith("+++ "):
            dst = line[4:].split("\t")[0].strip()
            if cur is not None:
                if dst == "/dev/null":
                    cur["deleted"] = True
                else:
                    cur["path"] = re.sub(r"^[ab]/", "", dst)
        elif line.startswith("@@"):
            m = re.search(r"@@ \-[\d,]+ \+(\d+)", line)
            hunk_line = int(m.group(1)) if m else None
        elif cur is not None:
            if line.startswith("+"):
                cur["added"].append({"no": hunk_line, "text": line[1:]})
                if hunk_line is not None:
                    hunk_line += 1
            elif line.startswith("-"):
                cur["removed"].append({"no": hunk_line, "text": line[1:]})
    return [f for f in files if f["path"] or f["added"] or f["removed"]]


# ---------------------------------------------------------------- 规则匹配
def match_layer(path, layer):
    low = path.lower()
    if any(fnmatch.fnmatch(low, g.lower()) for g in layer.get("globs", [])):
        return True
    return any(p.lower() in low for p in layer.get("paths", []))


def compile_sensitivities(rules):
    out = []
    for s in rules["sensitivities"]:
        flags = re.IGNORECASE if "i" in s.get("flags", "") else 0
        pats = [re.compile(r, flags) for r in s["regex"]]
        out.append((s, pats))
    return out


def risk_score(layers_hit, signals, rules):
    """风险驱动测试选择④：层级/信号去重加权求和 → 分级。"""
    cfg = rules.get("risk_model", {})
    lw, sw = cfg.get("layer_weights", {}), cfg.get("signal_weights", {})
    drivers, score = [], 0
    for lid in layers_hit:
        w = lw.get(lid, 1)
        score += w
        drivers.append({"source": "layer:" + lid, "weight": w})
    for s in sorted(signals):
        w = sw.get(s, 0)
        if w:
            score += w
            drivers.append({"source": s, "weight": w})
    th = cfg.get("thresholds", {"medium": 6, "high": 10})
    level = "low" if score < th["medium"] else ("medium" if score < th["high"] else "high")
    return {"score": score, "level": level, "drivers": drivers,
            "level_name": rules.get("risk_model", {}).get("level_names", {}).get(level, level)}


def link_trace(files, must_types, trace, rules, lang="en"):
    """③需求追溯 + ⑤经典TIA回归用例选择。路径命中优先，类型交集兜底。"""
    L = LANG[lang]
    if not trace:
        return {"requirements_linked": [], "regression_cases_selected": []}
    cfg = rules.get("trace", {})
    paths = [f["path"] for f in files]

    def hit(pats):
        return any(fnmatch.fnmatch(p, pat) for p in paths for pat in pats or [])

    reqs = [{"id": r["id"], "desc": r.get("desc", "")}
            for r in trace.get("requirements", []) if hit(r.get("paths"))]
    sel = []
    for c in trace.get("cases", []):
        if cfg.get("path_priority", True) and hit(c.get("paths")):
            sel.append({"id": c["id"], "name": c.get("name", ""), "reason": "path",
                        "detail": L["tia_path_detail"]})
        elif cfg.get("type_fallback", True):
            tm = sorted(set(c.get("types", [])) & set(must_types))
            if tm:
                sel.append({"id": c["id"], "name": c.get("name", ""), "reason": "type",
                            "detail": L["tia_type_detail"] + ",".join(tm)})
    return {"requirements_linked": reqs, "regression_cases_selected": sel}


def analyze(files, rules, bugfix, blast_radius, trace=None, lang="en"):
    must = {}       # type -> [evidence,...]
    cond = {}       # (type, condition) -> 已见层级/规则
    layers_hit = set()
    signals = set()

    def add_must(t, ev):
        lst = must.setdefault(t, [])
        if len(lst) < MAX_EVIDENCE_PER_TYPE:
            lst.append(ev)

    sens = compile_sensitivities(rules)

    for f in files:
        path = f["path"]
        # Step 1 层级
        for layer in rules["layers"]:
            if match_layer(path, layer):
                layers_hit.add(layer["id"])
                signals.add("layer:" + layer["id"])
                for t in layer["must"]:
                    add_must(t, {"file": path, "rule": "layer:" + layer["id"]})
                for c in layer.get("conditional", []):
                    cond.setdefault((c["type"], c["condition"]), "layer:" + layer["id"])
        # 删除文件 → 版本兼容风险提示
        if f.get("deleted"):
            add_must("regression", {"file": path, "rule": "file:deleted"})
            cond.setdefault(("version_compat", "整文件删除，确认无消费方引用"), "file:deleted")
        # Step 2 敏感性（只看新增行）
        for s, pats in sens:
            for ln in f["added"]:
                if any(p.search(ln["text"]) for p in pats):
                    signals.add("sens:" + s["id"])
                    for t in s["must"]:
                        add_must(t, {"file": path, "line": ln["no"], "rule": "sens:" + s["id"],
                                     "evidence": ln["text"].strip()[:120]})
                    for c in s.get("conditional", []):
                        cond.setdefault((c["type"], c["condition"]), "sens:" + s["id"])
                    break  # 每文件每规则记一条即可

    # Step 3 联动
    if bugfix:
        signals.add("flag:bugfix")
        for t in rules["bugfix"]["must_add"]:
            add_must(t, {"rule": "flag:bugfix", "evidence": LANG[lang]["bugfix_evidence"]})

    # ④ 风险分级
    risk = risk_score(layers_hit, signals, rules)

    # 回归范围：blast_radius（②依赖分析，外挂）> 风险分级 > 默认模块级
    scope = rules["regression_scope"]["default"]
    scope_reason = LANG[lang]["scope_default"]
    if blast_radius:
        esc = blast_radius.get("escalate")
        if esc is True or str(esc).lower() == "true":
            scope = rules["regression_scope"]["escalate"]
            scope_reason = LANG[lang]["scope_br"]
    if scope == rules["regression_scope"]["default"] and risk["level"] == "high":
        scope = rules["regression_scope"]["escalate"]
        scope_reason = LANG[lang]["scope_risk"] % (
            risk["score"], ", ".join("%s +w%d" % (d["source"], d["weight"])
                                     for d in risk["drivers"][:3]))

    # ③⑤ 追溯 + 经典 TIA 用例选择
    trace_res = link_trace(files, list(must.keys()), trace, rules, lang)

    return {
        "must": {t: ev for t, ev in sorted(must.items())},
        "conditional": [{"type": t, "condition": c, "from": src}
                        for (t, c), src in sorted(cond.items())],
        "regression_scope": {"level": scope, "reason": scope_reason},
        "risk": risk,
        "traceability": trace_res,
        "layers_touched": sorted(layers_hit),
        "signals": sorted(signals),
        "stats": {"files": len(files),
                  "added_lines": sum(len(f["added"]) for f in files),
                  "removed_lines": sum(len(f["removed"]) for f in files)},
    }


# ---------------------------------------------------------------- 报告
def print_report(res, lang="en"):
    L = LANG[lang]
    name = lambda t: L["types"].get(t, t)
    print("=" * 62)
    print(L["report_title"] % (len(res["must"]), len(res["conditional"]),
                               res["regression_scope"]["level"]))
    print("=" * 62)
    print("\n【%s】" % L["must"])
    for t, evs in res["must"].items():
        print("  ▶ %s (%s)" % (name(t), t))
        for e in evs:
            loc = e.get("file", "-") + ((":" + str(e["line"])) if e.get("line") else "")
            print("      - %s  [%s]" % (loc, e["rule"]))
            if e.get("evidence"):
                print("        %s: %s" % (L["evidence"].capitalize() if lang == "en" else L["evidence"], e["evidence"]))
    print("\n【%s】" % L["cond"])
    for c in res["conditional"]:
        print("  ○ %s (%s)  %s: %s  %s %s" % (name(c["type"]), c["type"], L["when"], c["condition"], L["from"], c["from"]))
    level_disp = L[res["risk"]["level"]]
    print("\n【%s】%s" % (L["risk"], L["risk_fmt"] % (
        level_disp, res["risk"]["score"],
        "; ".join("%s +w%d" % (d["source"], d["weight"]) for d in res["risk"]["drivers"]))))
    print("\n【%s】%s — %s" % (L["scope"], res["regression_scope"]["level"], res["regression_scope"]["reason"]))
    tr = res.get("traceability") or {}
    if tr.get("requirements_linked"):
        print("\n【%s】" % L["reqs"])
        for r in tr["requirements_linked"]:
            print("  ◆ %s  %s" % (r["id"], r["desc"]))
    if tr.get("regression_cases_selected"):
        print("\n【%s】" % L["cases"])
        for c in tr["regression_cases_selected"]:
            tag = L["path_match"] if c["reason"] == "path" else L["type_match"]
            print("  ● %s %s  [%s] %s" % (c["id"], c["name"], tag, c["detail"]))
    print("\n【%s】%s" % (L["layers"], ", ".join(res["layers_touched"])))
    print("【%s】%s" % (L["signals"], ", ".join(res["signals"])))
    print("【%s】%s" % (L["stats"], L["stats_fmt"] % (
        res["stats"]["files"], res["stats"]["added_lines"], res["stats"]["removed_lines"])))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="变更驱动的测试类型选择器（TIA 轻量实现）")
    ap.add_argument("--diff", help="unified diff 文件路径")
    ap.add_argument("--bugfix", action="store_true", help="本次变更为 bug 修复")
    ap.add_argument("--blast-radius", help="blast_radius.py 的 JSON 产物，联动回归范围")
    ap.add_argument("--trace", help="trace.json：需求/用例与代码路径映射（追溯③+经典TIA⑤）")
    ap.add_argument("--rules", help="外部规则文件（缺省用内置规则）")
    ap.add_argument("--out", help="结果写入 JSON 文件")
    ap.add_argument("--lang", choices=["en", "zh"], help="输出语言（默认 PR_LANG 环境变量/en）")
    ap.add_argument("--dump-rules", action="store_true", help="导出内置规则到 stdout 后退出")
    args = ap.parse_args()

    if args.dump_rules:
        json.dump(DEFAULT_RULES, sys.stdout, ensure_ascii=False, indent=2)
        return 0

    rules = DEFAULT_RULES
    if args.rules:
        with open(args.rules, encoding="utf-8") as fh:
            rules = json.load(fh)
    if not args.diff:
        ap.error("需要 --diff（或 --dump-rules）")
    try:
        with open(args.diff, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        print(LANG[resolve_lang(args.lang)]["err_read_diff"] % e, file=sys.stderr)
        return 2

    files = parse_diff(text)
    if not files:
        print(LANG[resolve_lang(args.lang)]["err_no_files"], file=sys.stderr)
        return 2

    br = None
    if args.blast_radius:
        try:
            with open(args.blast_radius, encoding="utf-8") as fh:
                br = json.load(fh)
        except (OSError, ValueError) as e:
            print(LANG[resolve_lang(args.lang)]["warn_bad_br"] % e, file=sys.stderr)

    trace = None
    if args.trace:
        try:
            with open(args.trace, encoding="utf-8") as fh:
                trace = json.load(fh)
        except (OSError, ValueError) as e:
            print(LANG[resolve_lang(args.lang)]["warn_bad_trace"] % e, file=sys.stderr)

    lang = resolve_lang(args.lang)
    res = analyze(files, rules, args.bugfix, br, trace, lang)
    print_report(res, lang)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print("\n" + LANG[lang]["written_to"] % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
