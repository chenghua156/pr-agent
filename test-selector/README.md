# test-selector —— 变更驱动的测试类型选择器（Phase 1 独立组件）

> 定位：Test Impact Analysis（TIA）/ [风险驱动测试选择](https://nhimg.org/glossary/risk-based-test-selection/)的轻量确定性实现。
> 输入 diff → 输出「必测类型 + 条件选测 + 回归范围」清单（`testtypes.json`），供用例生成
> skill 链与 CI 编排消费。**只做选型、不做执行、不做门禁**（exit 0 = 正常出报告）。

## 文件

| 文件 | 作用 |
|---|---|
| `test_selector.py` | 主脚本，仅依赖 Python3 标准库 |
| `rules.json` | 规则外置文件（`--dump-rules` 生成，可直接编辑：加层级 glob / 加敏感正则） |
| `SKILL.md` | 注册为 DSH skill 的说明文档 |
| `testtypes.json` | 示例产物（对 `pr-review-pipeline/testdata/buggy-pay.diff` 的判定结果） |

## 三步判定法（与脚本规则一一对应）

| 步骤 | 依据 | 产出 |
|---|---|---|
| Step 1 层级规则 | 文件路径 glob/子串 → UI / API / 逻辑 / DB / 配置 / 依赖 / 测试 | 该层的必测类型 |
| Step 2 敏感性规则 | 新增行正则 → 硬编码密钥 / SQL拼接 / 命令执行 / 并发 / 资金事务 / 热路径 / 句柄泄漏 | 追加必测 + 条件选测（附证据行） |
| Step 3 联动信号 | `--bugfix` → 复现用例+回归；`--blast-radius` escalate=true → 回归范围 module→chain | 范围升级 |

## 变更影响分析五分类覆盖对照

| 分类 | 实现位置 | 说明 |
|---|---|---|
| ① Change-based 变更分析 | `layers` + `sensitivities` | diff 路径分层 + 内容敏感性正则（内建） |
| ② Dependency 依赖分析 | `--blast-radius` 外挂 | 复用流水线 `blast_radius.py` 反向调用链，escalate→链路级回归 |
| ③ Traceability 需求追溯 | `--trace trace.json` 的 `requirements` | 需求↔代码路径映射，报告输出关联需求 |
| ④ Risk-based 风险驱动 | `risk_model`（层级/信号权重→风险分→分级） | 高风险自动升级回归范围（与②取或） |
| ⑤ Test Impact Analysis | `--trace trace.json` 的 `cases` | 改动路径→选中应跑用例（path 优先，类型∩必测兜底） |

`trace.json` 契约见 `trace.example.json`：`requirements[].paths` / `cases[].paths` 用
fnmatch 通配（`*` 含 `/`），`cases[].types` 用测试类型枚举（同 TYPE_NAMES 键）。
映射文件来源建议：需求侧从 Bugzilla/需求单导出，用例侧从用例管理库打路径标签。

## 用法

```bash
# 1. 日常：看一次改动该测什么
python3 test_selector.py --diff pr.diff

# 2. bug 修复：追加 复现用例+回归
python3 test_selector.py --diff pr.diff --bugfix --out testtypes.json

# 3. 与 blast_radius 联动（escalate 时升级链路级回归）
python3 ../pr-review-pipeline/scripts/blast_radius.py --diff pr.diff --repo . > br.json
python3 test_selector.py --diff pr.diff --blast-radius br.json

# 3b. 追溯 + 经典 TIA 选例（需求/用例路径映射）
python3 test_selector.py --diff pr.diff --bugfix --trace trace.example.json --out testtypes.json

# 4. 编辑规则后生效（或 --rules rules.json 显式指定）
python3 test_selector.py --dump-rules > rules.json   # 重新导出再改
```

## 产物契约（testtypes.json）

```json
{
  "must":        {"security": [{"file": "...", "line": 4, "rule": "sens:hardcoded_secret", "evidence": "..."}]},
  "conditional": [{"type": "fuzz", "condition": "外部输入面大", "from": "sens:auth_surface"}],
  "regression_scope": {"level": "module|chain", "reason": "..."},
  "layers_touched": ["backend_logic"],
  "signals": ["sens:money_tx", "..."],
  "stats": {"files": 1, "added_lines": 43, "removed_lines": 0}
}
```

下游消费方式：
- **skill 链**：`dp-project-testcase`（diff+设计文档→CSV 用例）把 `must` 当必覆盖类型输入 →
  `testpoint-completion` 扩写 → `auto-testcase-script` 出 Robot 脚本；
- **CI 编排**：`must` 含性能类 → 触发 JMeter/TRex 任务；含安全类 → 触发 ZAP/gitleaks；
  `conditional` 交给 agent 按上下文判读取舍。

## 融合路径（Phase 2，接入 pr-review-pipeline）

1. `pipeline.sh`：在 `size_gate` 之后插入
   `python3 test-selector/test_selector.py --diff pr.diff --bugfix --out out/testtypes.json`
   （bug 单场景由 dp-review-bull 传入 `--bugfix`；blast_radius 产物在同目录可直接联动）；
2. `ai-review.yml` / `Jenkinsfile`：归档 `testtypes.json`，作为"测试选型"步骤展示；
3. 与 Stage 6 台账联动：记录每个 bug 实际消耗的测试类型，反哺 `rules.json` 阈值
   （某类误报多 → 收窄正则；漏测多 → 加规则）。

## 已知边界

- 敏感性正则为启发式（同 blast_radius 的 grep 路线）：看重召回，精确取舍交给下游；
- 条件选测需要人或 agent 判读 `condition` 后取舍；
- ③⑤ 的精度取决于 `trace.json` 映射质量：经典 TIA 严格版用 coverage 数据
  （cobertura 行级映射）替代路径通配，属 Phase 2 升级项（接 `coverage_delta.py` 产物）；
- 风险权重为首版经验值，建议按台账回归：某类误升级多→降权重，漏升级→加规则；
- 层级规则目前覆盖常见 Web/后端工程布局，团队目录习惯不同请改 `rules.json` 的 paths/globs。
