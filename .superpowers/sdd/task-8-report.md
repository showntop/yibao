# Task 8 前置修复报告：attach/join 落 busy 工位的路由表空洞

## 状态

完成。闸门全绿：`pnpm test` 10 文件 132 通过（基线 132 绿保持）；`pnpm build` ✓ built in 2.58s；`pnpm typecheck`（vue-tsc --noEmit）无错误。

## 问题（T6 评审登记的 Important follow-up）

壳 App.vue 的 join/attach 路径先 `stations.bind(target, sid, agent)` 写路由表，再 `stationRefs[target]?.bindSession(sid, agent)`；bindSession 有 busy 守卫（工位 streaming/sending 中拒理）——拒理时路由表已绑但工位未绑，该 sid 后续流事件按路由表投递进工位后被 sid 过滤丢弃（黑洞）。

## 修法（按简报 5 点）

1. **壳加目标选择守卫**：新增 `pickIdleTarget()`（App.vue:67）——先取 `stations.pickBindTarget()`，若该工位 `isBusy` 改找第一个非 busy 工位；全部 busy 返回 null（不 bind 不投），并经 `stationRefs[focusId]?.hint(...)` 给聚焦工位状态行提示「所有工位都在忙，先停止一个再加入会话」。attach 分支（onInit）与 `join` 处理器共用。
2. **状态行通道**：StationView 新增 expose `hint(text: string, err?: boolean)`（StationView.vue:454），内部即调既有 `onComposerStatus`（tip 机制），注释写明「壳侧提示通道」。
3. **双保险回滚**：`bindSession` 由 void 改返回 boolean（受理 true / busy 守卫拒绝 false；守卫与状态行提示保留）。壳侧新增 `bindStation()`（App.vue:78）——路由表先绑 → bindSession；拒理时回滚 `stations.unbind(target)`，但仅当该工位路由表里仍是这个 sid（防误滚新绑）。
4. `unbindSession` 保持 void 未动。
5. 中文注释全角标点、风格一致；stations store 未动（守卫在壳，store 层无可纯逻辑测试的守卫点，未加测试）。

## 改动文件

- `plugins/coding/panel/src/App.vue`：`StationViewExposed` 接口（bindSession 返回 boolean + 新增 hint）、新增 `pickIdleTarget`/`bindStation`、attach 分支与 `join` 改走两辅助函数、头部 demux 注释同步。
- `plugins/coding/panel/src/components/StationView.vue`：`bindSession` 返回 boolean、新增 `hint`、defineExpose 与头注 expose 清单同步。

## 闸门输出

```
pnpm test      → Test Files 10 passed (10) / Tests 132 passed (132)（基线 132 绿保持）
pnpm build     → ✓ built in 2.58s（chunk >500kB 警告为既有基线警告）
pnpm typecheck → vue-tsc --noEmit 无错误
```

## Concerns

- 壳逻辑无组件测试基建（工程惯例），本修复保障靠 typecheck + build + 132 例 store/lib 测试闸；busy 守卫在壳（读 expose 的 isBusy），非 store 纯逻辑，故无新增单测。
- 预挂载窗内 stationRef 缺失时 `isBusy` 读不到按非 busy 处理——未挂载工位不可能在跑流，语义正确；全忙提示在聚焦工位 ref 缺失时静默丢弃（预挂载窗内无可见工位可提示，可接受）。
