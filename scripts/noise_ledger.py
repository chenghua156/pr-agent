#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 6 噪音台账管理 —— SNR 随时间上升的唯一杠杆。

Google learnings 机制的开源等价物：被 👎 的评论模式沉淀为结构化模式，
分诊层（triage.py）每周装载本台账自动剔除同类误报。

命令：
  init                          初始化台账文件
  add "模式描述" --match k1,k2 --reason "原因" [--module services/user]
                                沉淀一条误报模式（match 关键词需全部命中才匹配）
  vote <comment_id> up|down [--module m]   记录一条评论的人工反馈
  report [--days 7]             输出 SNR 周报（有用率、按模块分组）

依赖：仅 Python3 标准库
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

DEFAULT_LEDGER = "config/noise_ledger.json"


def load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"patterns": [], "votes": []}


def save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def next_pattern_id(patterns):
    nums = [int(p["id"][1:]) for p in patterns if p.get("id", "").startswith("N") and p["id"][1:].isdigit()]
    return f"N{max(nums, default=0) + 1:03d}"


def cmd_init(args, data):
    save(args.ledger, data)
    print(f"[ledger] 初始化台账：{args.ledger}")


def cmd_add(args, data):
    if not args.match:
        sys.exit("错误：--match 至少提供一个关键词（逗号分隔，全部命中才匹配）")
    entry = {
        "id": next_pattern_id(data["patterns"]),
        "module": args.module or "*",
        "pattern": args.pattern,
        "match": [k.strip() for k in args.match.split(",") if k.strip()],
        "reason": args.reason or "",
        "added_at": datetime.now().strftime("%Y-%m-%d"),
        "added_by": os.environ.get("USER", "unknown"),
    }
    data["patterns"].append(entry)
    save(args.ledger, data)
    print(f"[ledger] 沉淀误报模式 {entry['id']}：{entry['pattern']}（module={entry['module']}）")
    print(f"[ledger] 当前台账共 {len(data['patterns'])} 条模式")


def cmd_vote(args, data):
    data["votes"].append({
        "comment_id": args.comment_id,
        "rating": "up" if args.rating == "up" else "down",
        "module": args.module or "*",
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save(args.ledger, data)
    print(f"[ledger] 记录反馈 comment={args.comment_id} rating={args.rating}")


def cmd_report(args, data):
    cutoff = datetime.now() - timedelta(days=args.days)
    votes = [v for v in data.get("votes", [])
             if datetime.strptime(v["at"][:10], "%Y-%m-%d") >= cutoff]
    up = sum(1 for v in votes if v["rating"] == "up")
    down = len(votes) - up
    total, snr = len(votes), None
    if total:
        snr = f"{up / down:.2f}:1" if down else "∞（零误报反馈）"
    print(f"=== 噪音台账周报（近 {args.days} 天）===")
    print(f"评论反馈：{total} 条  有用(👍)={up}  误报(👎)={down}  有用率={'' if not total else f'{up / total:.0%}'}  SNR={snr}")
    print(f"沉淀误报模式：{len(data.get('patterns', []))} 条（分诊层自动装载）")

    by_module = {}
    for v in votes:
        by_module.setdefault(v.get("module", "*"), []).append(v)
    if len(by_module) > 1:
        print("\n按模块：")
        for mod, vs in sorted(by_module.items()):
            u = sum(1 for v in vs if v["rating"] == "up")
            print(f"  {mod}: {len(vs)} 条反馈，有用率 {u / len(vs):.0%}")
    # 红线检查（方案 §1：有用率 <40% 持续两周应回退配置）
    if total >= 10 and up / total < 0.40:
        print("\n⚠️  红线告警：有用率低于 40%，请按 README 回退流程处理")


def main():
    ap = argparse.ArgumentParser(description="噪音台账管理（Stage 6）")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help="台账 JSON 路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    p_add = sub.add_parser("add", help="沉淀一条误报模式")
    p_add.add_argument("pattern", help="模式描述，如：防御性重试被误判为死代码")
    p_add.add_argument("--match", required=True, help="逗号分隔关键词，全部命中才匹配")
    p_add.add_argument("--reason", help="判定为误报的原因")
    p_add.add_argument("--module", help="所属模块路径（大仓分模块维护）")
    p_add.set_defaults(fn=cmd_add)

    p_vote = sub.add_parser("vote", help="记录一条评论的人工反馈")
    p_vote.add_argument("comment_id")
    p_vote.add_argument("rating", choices=["up", "down"])
    p_vote.add_argument("--module")
    p_vote.set_defaults(fn=cmd_vote)

    p_rep = sub.add_parser("report", help="输出 SNR 周报")
    p_rep.add_argument("--days", type=int, default=7)
    p_rep.set_defaults(fn=cmd_report)

    args = ap.parse_args()
    args.fn(args, load(args.ledger))


if __name__ == "__main__":
    main()
