#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 1 覆盖率 delta 门禁 —— 测试兜底的行为回归网入口。

对比 base/head 两份 Cobertura 格式覆盖率报告（jacoco/cobertura/coverage.py 均可导出），
执行方案规则：
  1. 总覆盖率不得下降（allow_decrease=false）
  2. 覆盖率须 >= new_code_min_pct（简化按总体执行；精确新增行覆盖见 README 升级路径）

用法：
  python3 coverage_delta.py --base base.xml --head head.xml [--config config/pipeline.json]
退出码：0 通过 / 2 门禁失败（CI 直接 fail）
依赖：仅标准库
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

DEFAULT_CONFIG = {"coverage": {"new_code_min_pct": 80, "allow_decrease": False}}


def line_rate_pct(xml_path):
    """读 Cobertura <coverage line-rate="0.83"> -> 83.0；兼容无属性行为报错。"""
    root = ET.parse(xml_path).getroot()
    rate = root.attrib.get("line-rate")
    if rate is None:
        sys.exit(f"错误：{xml_path} 不是合法 Cobertura 报告（缺少 line-rate）")
    return float(rate) * 100.0


def package_rates(xml_path):
    """按 package 输出覆盖率，供 --verbose 展示热点包。"""
    root = ET.parse(xml_path).getroot()
    return {p.attrib.get("name", "?"): float(p.attrib.get("line-rate", 0)) * 100.0
            for p in root.iter("package")}


def main():
    ap = argparse.ArgumentParser(description="Stage 1 覆盖率 delta 门禁")
    ap.add_argument("--base", required=True, help="基线（目标分支）Cobertura XML")
    ap.add_argument("--head", required=True, help="当前 PR Cobertura XML")
    ap.add_argument("--config", default="config/pipeline.json")
    ap.add_argument("--verbose", action="store_true", help="打印各 package 覆盖率")
    args = ap.parse_args()

    cfg = DEFAULT_CONFIG
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            user = json.load(f)
        cfg["coverage"].update(user.get("coverage") or {})

    base_pct, head_pct = line_rate_pct(args.base), line_rate_pct(args.head)
    delta = head_pct - base_pct
    print(f"[coverage] base={base_pct:.1f}%  head={head_pct:.1f}%  delta={delta:+.1f}%")

    if args.verbose:
        for name, pct in sorted(package_rates(args.head).items(), key=lambda kv: kv[1]):
            print(f"    {pct:5.1f}%  {name}")

    rules = cfg["coverage"]
    failures = []
    if not rules.get("allow_decrease", False) and delta < -0.05:
        failures.append(f"总覆盖率下降 {abs(delta):.1f}%（allow_decrease=false）")
    if head_pct < float(rules.get("new_code_min_pct", 80)):
        failures.append(f"覆盖率 {head_pct:.1f}% 低于门槛 {rules.get('new_code_min_pct')}%")

    if failures:
        print("[coverage] ❌ 门禁失败：")
        for msg in failures:
            print(f"    - {msg}")
        sys.exit(2)
    print("[coverage] ✅ 通过")


if __name__ == "__main__":
    main()
