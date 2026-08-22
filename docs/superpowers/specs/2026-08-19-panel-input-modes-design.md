# 面板输入模式声明(handoff 扩到大窗)spec

日期:2026-08-19 | 前篇:specs/2026-08-19-input-handoff-design.md(输入条 handoff,已落地 f646319)

## 背景

输入条 handoff 只覆盖了面板窗(PanelApp),且判定硬编码 `panel === "coding:studio"`。大窗(主窗插件页 HomePlugins)底部仍是「Composer + 译宝条」双框,观感问题与面板窗同宗。本 spec 把「壳输入条怎么办」做成**插件声明式配置**,同时把 handoff 扩到大窗。

## 输入模式(MECE)

「插件有无输入框 × 壳条在不在」四种组合,互斥穷尽:

| 模式 | 插件框 | 壳条 | 语义 |
|---|---|---|---|
| `inherit`(缺省) | 无 | 在 | 壳条是唯一输入(现状一切 schema 面板) |
| `coexist` | 有 | 在 | 并存(边操作边问译宝的面板) |
| `handoff` | 有 | 无 | 插件接管输入,壳条让位(coding studio) |
| `none` | 无 | 无 | 纯展示面板 |

**壳侧行为只有二值**:`input ∈ {handoff, none}` → 壳条隐藏,其余不动。四值是给插件作者的语义声明,不是四套实现。

## 关键设计决策

### A. manifest 声明

`[[panel]]` 新增可选 `input`,合法值 `inherit|coexist|handoff|none`,缺省 `inherit`;非法值 warn 并回退缺省。coding 的 studio 面板声明 `input = "handoff"`。

### B. 透传链(最小改动)

- sidecar `plugins.py`:`_load_panels` module 分支保留 `input`(现状 module 分支只留 type/entry,其余字段丢弃);`_surface_decl_from` 透传(与 surfaces/min_width 同先例)——一处改,5 个发送点(loop.py×2、server.py×3)全带上。Rust 零改动(payload 整体缓存透传)。
- `desktop/src/lib/brain.ts` `PanelPayload` 加 `input?: "inherit"|"coexist"|"handoff"|"none"`;PanelApp/HomePlugins 的 current 类型、panel 事件构造、pullCache 泛型同步补字段(PanelApp 现状连 hints 都没挑,是显式挑字段截断,必须主动加)。

### C. 两个宿主表面

- PanelApp:**让位** computed 读声明——`input ∈ {handoff, none}` 即隐藏壳条(收编硬编码;none 下团子逃生口同样在标题栏);**草稿随迁仅 `handoff`**(none 面板没有 Composer,随迁等于丢稿)。既有随迁/逃生口其余逻辑零改动。
- HomePlugins:底部 bench 条加同样的让位判定(现状无条件渲染,HomePlugins.vue:744)。**大窗逃生口不加新元素**——顶部「主屏」tab 就是,点回去即找译宝。
- 宠物窗 InputBar 不动(不是面板宿主);mobile/peek 无壳条,声明天然 no-op;scene 工作面复用 HomePlugins,handoff 面板激活时一致让位(语义自洽)。

### D. 测试

- sidecar pytest:合法值解析/缺省回退/非法值 warn+回退/载荷透传。
- desktop vitest:PanelApp 读声明让位(原 handoff 测试载荷补 `input` 字段——现有用例不带字段会红,必须同步改);HomePlugins 让位/恢复用例。
- panel 工程零改动。

## 非目标

- coexist/none 的声明方(无真实插件用,仅定义语义与行为)
- 宠物窗/主屏/数据 tab 的输入条行为
- 壳条让位的动画过渡(同前篇:v-if 直切)

## 验收标准

A. coding manifest 声明 `input = "handoff"` 后,面板窗与大窗打开 studio 都只剩 Composer 一个输入框;切走/回主屏壳条原样恢复
B. 不声明 input 的面板(tools 等)行为零变化;非法值 warn 且按 inherit 处理
C. PanelApp 无 coding:studio 硬编码残留(handoff 判定只读声明)
D. sidecar pytest / desktop vitest / panel vitest 全绿
