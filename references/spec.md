# 规格文件字段全表

规格是一个 UTF-8 JSON。最小可用结构只有 `nodes` 和 `edges`。

## 顶层

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `title` | str | — | 图名。**不要内嵌**——图名写在回复正文第一行；仅在用户明确要求时才配合 `show_title: true` |
| `show_title` | bool | `false` | 是否把 `title`/`subtitle`/`note` 画进图里。**默认关闭且不建议打开**（教材插图只有图形） |
| `subtitle` | str | — | 副标题/说明行。只在 `show_title: true` 时渲染，教材体例不用 |
| `note` | str | — | 页脚注。只在 `show_title: true` 时渲染，教材体例不用 |
| `theme` | str | `textbook` | `textbook` 黑白线稿 / `soft` 浅色填充 |
| `legend` | bool | `false` | 是否画底部图例。教材范例不带图例，**保持关闭** |
| `legend_flow` | bool | `false` | 追加第 6 项"业务流向（块状空心箭头）" |
| `edge_labels` | bool | `false` | **已移除，无效**。连线文字在渲染器中已无对应代码路径，设为 `true` 只会被忽略并告警 |
| `doc_style` | str | `wave` | `wave` 波浪底边（样本画法）/ `fold` 折角纸张 |
| `unit_shape` | str | `auto` | `auto` 短标签正圆、长标签椭圆 / `circle` 全用正圆 / `ellipse` 全用椭圆 |
| `direction` | str | `LR` | 仅在未给 `col`/`row` 时生效：`LR` 横排 / `TB` 竖排 |
| `nodes` | array | 必填 | 节点列表 |
| `edges` | array | 必填 | 连线列表 |
| `fs` `edge_fs` `pad` `max_node_w` `line_height` `hgap` `vgap` `stub` `arrow_len` `edge_width` | num | 见下表 | 等价于写进 `options`，顶层直写优先 |

也可以写成 `"options": { "hgap": 40, "theme": "soft" }`，顶层同名字段会覆盖它。

排版参数默认值：

| 参数 | 默认 | 含义 |
|---|---|---|
| `fs` | 13 | 节点字号 |
| `edge_fs` | 11 | 连线标签字号 |
| `pad` | 8 | 形状内文字留白 |
| `max_node_w` | 132 | 形状最大宽度，超了自动折行 |
| `line_height` | 1.28 | 行高倍数 |
| `hgap` | 34 | 列间距 |
| `vgap` | 52 | 行间距 |
| `stub` | 18 | 锚点出线的第一段直腿长度 |
| `arrow_len` | 9 | 箭头长度（箭尖贴边框，线段自动回缩） |
| `edge_width` | 1.4 | 连线粗细 |

## nodes[]

| 字段 | 别名 | 说明 |
|---|---|---|
| `id` | `name` | 必填，唯一，供连线引用 |
| `type` | `t` | `unit` / `proc` / `doc` / `store` / `card`，也接受中文（`单位` `处理` `单据` `存储` `卡片`） |
| `label` | `l` `text` | 框内文字，支持 `\n` 强制换行 |
| `col` | `c` | 列号（整数，决定左右位置） |
| `row` | `r` | 行号（整数，决定上下位置） |
| `span` | — | 横跨几列（做宽条幅用），默认 1 |
| `w` `h` | — | 强制宽高 px，一般不要写，交给自动测量 |
| `fs` | — | 该节点单独字号 |
| `pad` | — | 该节点单独内边距 |
| `maxw` | — | 该节点单独最大宽度 |

不写 `col`/`row` 时按拓扑层级自动分层（入度为 0 的节点作为第 0 列）。自动布局只适合快速预览。

## edges[]

| 字段 | 别名 | 默认 | 说明 |
|---|---|---|---|
| `from` | `src` `a` | 必填 | 起点节点 id |
| `to` | `dst` `b` | 必填 | 终点节点 id |
| `label` | `l` | — | **已废弃，不渲染**。TFD 连线上从不写文字（教材范例全程如此），写了会被丢弃并在 stderr 告警 |
| `fromSide` | `fs` | 自动 | `top`/`bottom`/`left`/`right`（可写 `t`/`b`/`l`/`r`） |
| `toSide` | `ts` | 自动 | 同上 |
| `at0` `at1` | — | 自动 | 0–1，锚点在该边上的位置；同一侧多条箭头脚本会自动分散成 0.33/0.67… |
| `route` | — | `auto` | `auto` / `straight`（直连）/ `hvh` `hv` `h`（先横）/ `vhv` `vh` `v`（先竖）/ `poly` |
| `bend` | — | 自动 | 折点坐标：`route` 先横时是 x，先竖时是 y |
| `via` | — | — | 配合 `route: "poly"`，绝对坐标点数组 `[[x,y], ...]`，用于手工绕行 |
| `style` | `arrow` | `line` | `line` 细单箭头（数据流动）；`block` 块状空心箭头（业务流向） |

方向参数能省则省。脚本的自动选边规则是：
同行 → 左右边直连；同列 → 上下边直连；其余按位移主方向选边，并对几乎对齐的端点做吸附，避免出现几像素的小台阶。显式指定的边若导致折线穿框，会自动退回自动选边。

## 命令行

```bash
python tfd.py spec.json -o out.svg          # 生成
python tfd.py spec.json --theme soft        # 浅色版
python tfd.py spec.json --no-legend         # 不画图例
python tfd.py spec.json --title "自定义标题"   # 给了 --title 会自动开启 show_title
```

成功输出 `已生成 out.svg (宽x高, N 个节点, M 条连线)`；节点重叠会在 stderr 打 `提示: 节点重叠: a 与 b`。
规格写错（id 重复、连线指向不存在的节点、type 不认识）会返回退出码 2 并说明字段。
