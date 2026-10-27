# tfd-flow-diagram — 业务流程图（TFD）SVG 生成技能

一个开放格式的 **Agent Skill**（`SKILL.md` + 脚本 + 参考文档）：把一段业务描述变成符合**国内管理信息系统教材体例**的业务流程图（TFD / Transaction Flow Diagram，又叫系统流程图、事务流程图），输出可直接插入文档 / PPT 的 SVG。

**不绑定任何平台**——只要你的 AI 助手支持 [Agent Skills](https://agentskills.io) 规范（或类似的 SKILL.md 技能机制）就能安装使用；也可以完全脱离 AI，直接当命令行工具用。

## 特性

- **六类规范符号**，与教材范例一致：
  - 业务处理单位（圆 / 椭圆）：读者、库工、采购员、会计……
  - 业务处理功能（矩形）：登记、审核、汇总……
  - 单证报表（底边波浪的纸张形）：入库单、领料单、订货单……
  - 数据存储（左端带窄格的开口方框）：库存台账、档案……
  - 卡片（切角形）：登记卡、资料卡……
  - 数据流（细箭头）与业务流向（空心块状箭头）
- **网格布局 + 正交走线引擎**：列宽行高自适应、按列居中，自动选边、落点分散
- **冲突自动规避**：线条不交叉、不共线重合、不穿框、不贴边；同侧多线时自动放大节点、加大间距
- **零依赖**：纯 Python 3 标准库，无第三方包，pip 都不用装

## 方式一：安装为 Agent Skill

把整个 `tfd-flow-diagram/` 目录复制到你的 AI 助手的技能目录：

| 工具 | 用户级目录 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/`（Windows：`C:\Users\<用户名>\.workbuddy\skills\`） |
| Claude Code | `~/.claude/skills/` |
| 其他支持 SKILL.md 的工具 | 参见其文档中的 skills 目录约定 |

技能触发语：「画业务流程图」「画 TFD」「把××业务画成流程图」。

## 方式二：纯命令行使用（无需任何 AI）

```bash
# 基本用法：输入 JSON 规格，输出 SVG
python scripts/tfd.py spec.json -o out.svg

# 可选参数
python scripts/tfd.py spec.json -o out.svg \
  --theme textbook     # textbook（默认，教材黑白）| soft（浅色填充）
# --title "材料入库业务流程图"   覆盖标题
# --no-legend                   不画图例
```

要求：Python 3.8+，无任何第三方依赖。

规格文件（JSON）示例：

```json
{
  "title": "材料入库业务流程图",
  "nodes": [
    { "id": "buyer",  "type": "unit",  "label": "采购员", "col": 0, "row": 0 },
    { "id": "order",  "type": "doc",   "label": "入库单", "col": 1, "row": 0 },
    { "id": "check",  "type": "proc",  "label": "复核入库单", "col": 2, "row": 0 },
    { "id": "file",   "type": "store", "label": "入库文件", "col": 2, "row": 1 }
  ],
  "edges": [
    { "from": "buyer", "to": "order" },
    { "from": "order", "to": "check" },
    { "from": "check", "to": "file", "label": "留底" }
  ]
}
```

- `col` / `row` 为整数网格坐标：同一行 = 一条主线，同一列 = 上下串联。
- 连线一般无需指定方向，引擎自动选边并分散落点；特殊需求可加 `fromSide` / `toSide` / `bend` / `via`。
- 默认不渲染标题、图例与连线文字（教材插图只有图形本身）。标题 / 图例可用 `"show_title": true` / `"legend": true` 打开；**连线文字没有开启方式**——TFD 连线上从不写字，`edge.label` 写了也会被丢弃并告警。

完整字段见 [`references/spec.md`](references/spec.md)。

## 示例

`assets/` 下附四张完整示例（JSON 规格 + 渲染结果）：

| 示例 | 布局 | 展示要点 |
|---|---|---|
| [材料入库](assets/example-inbound.svg) | 纵向 | 侧挂岗位与台账 |
| [材料出库（发料）](assets/example-outbound.svg) | 纵向 | 细线 + 空心块状箭头双箭头、两条回流线分道 |
| [月底结账](assets/example-month-end.svg) | 纵向 | 处理与台账读写成对双向箭头、横椭圆 |
| [图书借阅](assets/example-library.svg) | 横向 | 多条主线并行（23 节点 / 26 条线） |

快速试用：

```bash
python scripts/tfd.py assets/example-inbound.json -o /tmp/inbound.svg
```

## 目录结构

```
tfd-flow-diagram/
├── SKILL.md              # 技能说明（Agent Skills 规范，各工具读取）
├── scripts/tfd.py        # 渲染器（纯 Python 标准库）
├── references/
│   ├── symbols.md        # 六类符号的语义、画法、常见误用
│   ├── spec.md           # 规格字段全表
│   └── layout.md         # 布局套路、走线技巧、易错点
└── assets/               # 四张完整示例（JSON + SVG）
```

## License

MIT
