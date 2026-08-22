# Final review fixes

## Fix 1 - cold-start explicit open matching

- File: `desktop/src/App.vue`
- Locations: plugin refs and loading around `allPlugins` / `plugins`, `loadPlugins()`, `submit()`, and `onMounted()`.
- Change: `loadPlugins()` now stores the full `list_plugins` result in `allPlugins` and derives the existing capped launcher view with `plugins = allPlugins.slice(0, 8)`.
- Change: `onMounted()` awaits `loadPlugins()`, matching the existing mount-time `list_plugins` pattern in `HomeChat.vue` and `AgentBrain.vue`.
- Change: `submit()` calls `matchExplicitOpen(text, allPlugins.value)` so the narrow open-intent rule can match any installed plugin, not just the first 8 launcher cards.
- UI impact: the plugin view still renders `plugins`, so its 8-entry cap is unchanged.

## Fix 2 - explicit window lifecycle

- File: `desktop/src/App.vue`
- Locations: `markExplicit()`, new `clearExplicit()`, `launchPlugin()`, `submit()`, and the `onEvent()` switch.
- Change: `markExplicit(pluginId)` records only `requestedPlugin`; the wall-clock `requestedUntil` deadline was removed.
- Change: `submit()` clears stale explicit state at the start of each submission, then marks the matched plugin immediately before `runInput()`.
- Change: `case "panel"` now reads `requestedPlugin === plugin` and no longer unconditionally clears the explicit flag after a panel event.
- Change: direct `launchPlugin()` failure and `submit()` send failure clear the explicit flag to avoid stale state when no run can complete.

Run-termination clearing points found in the existing `onEvent()` switch:

- `final_reply`: final assistant reply. Panel events are emitted during the loop before this terminal reply, so clearing here cannot race the panel consumer.
- `error`: run/error path. Existing branch resets `state`, clears streaming, pushes a warning, and flashes error.
- `interrupted`: interruption path. Existing branch marks or adds the halted reply and returns to idle.

I did not use `speaking_done` because it is a speech playback state transition rather than the run's model/tool loop termination.

## Fix 3 - surface title prefix stripping

- File: `desktop/src/App.vue`
- Location: `case "panel"` before constructing `SurfaceAttr`.
- Change: the sidecar title is split on `" · "`, trimmed, filtered, and the last usable segment is used as the surface title; if no usable segment exists, the original title is preserved.
- Result: a sidecar title like `闪念盘 · 闪念列表` renders through `SurfaceLine.vue` as `闪念列表 · N 条`, without changing `SurfaceLine.vue`.

## Gates

Command:

```sh
cd /Users/denny/Work/yibao-refactor/desktop && npx vue-tsc --noEmit
```

Output:

```text

```

Exit code: 0

## 修复轮次 2

### Fix 1 - run_done 清理显式打开标记

- File: `desktop/src/App.vue`
- Locations: import 列表新增 `onRunDone`；显式标记注释在 `requestedPlugin` 声明处更新；`onMounted()` 的 brain 事件订阅旁新增 `unlistenRunDone = await onRunDone(() => clearExplicit())`；`onUnmounted()` 新增 `unlistenRunDone?.()`。
- Change: 成功的插件视图直调路径现在会在 sidecar 写入 `brain-run-done` 后清理 `requestedPlugin`，避免标记泄漏到后续 panel 事件或语音会话。
- Matched cleanup pattern: 复用本文件已有的 `let unlistenX: (() => void) | null = null` 变量、`onMounted()` 中保存订阅返回值、`onUnmounted()` 中用 `unlistenX?.()` 释放的模式，位置紧邻 `onBrainEvent` / `onBrainStatus` 订阅。

### Fix 2 - error 不再清理显式标记

- File: `desktop/src/App.vue`
- Location: `onEvent()` switch 的 `case "error"`。
- Change: 移除 `clearExplicit()`，保留 `final_reply`、`interrupted`、`submit()` 起始处、发起失败路径和新增 `run_done` 的清理。

### Fix 3 - 插件加载不阻塞事件订阅

- File: `desktop/src/App.vue`
- Location: `onMounted()`。
- Change: `await loadPlugins()` 改为 `void loadPlugins()`；`loadPlugins()` 自带 try/catch，挂载流程不再等待插件 IPC 后才注册 brain 事件监听。

### Gates

Command:

```sh
cd /Users/denny/Work/yibao-refactor/desktop && npx vue-tsc --noEmit
```

Output:

```text

```

Exit code: 0

Command:

```sh
cd /Users/denny/Work/yibao-refactor/desktop && npx vite build
```

Output:

```text
vite v6.4.3 building for production...
transforming...
✓ 152 modules transformed.
rendering chunks...
computing gzip size...
dist/snip.html                                        0.75 kB │ gzip:  0.42 kB
dist/invoke.html                                      0.91 kB │ gzip:  0.44 kB
dist/design.html                                      1.43 kB │ gzip:  0.62 kB
dist/panel.html                                       1.57 kB │ gzip:  0.65 kB
dist/index.html                                       1.65 kB │ gzip:  0.69 kB
dist/home.html                                        2.01 kB │ gzip:  0.81 kB
dist/assets/logo-CeZ2Ys50.png                        56.13 kB
dist/assets/brain-shell-CZH-UYBJ.png                275.94 kB
dist/assets/WebviewPanel-B7KNpFsN.css                 0.14 kB │ gzip:  0.13 kB
dist/assets/snip-IJI7BwYS.css                         0.66 kB │ gzip:  0.35 kB
dist/assets/invoke-DrrXmXL0.css                       1.10 kB │ gzip:  0.45 kB
dist/assets/surface-policy-BNUeB8WN.css               2.92 kB │ gzip:  0.72 kB
dist/assets/Bubble-YUia62IA.css                       4.02 kB │ gzip:  1.12 kB
dist/assets/InputBar-ClGleGNi.css                     4.89 kB │ gzip:  1.33 kB
dist/assets/YbIcon-DHORsLoG.css                       6.13 kB │ gzip:  1.39 kB
dist/assets/panel-COsRh8Bt.css                        6.51 kB │ gzip:  1.57 kB
dist/assets/SchemaPanel-_3BMFDRB.css                  8.41 kB │ gzip:  1.77 kB
dist/assets/_plugin-vue_export-helper-L5Vw3bEE.css   12.36 kB │ gzip:  2.69 kB
dist/assets/design-j893C5Es.css                      14.49 kB │ gzip:  2.42 kB
dist/assets/main-Djl94cQG.css                        14.84 kB │ gzip:  3.14 kB
dist/assets/home-Cyq9cw8f.css                        84.28 kB │ gzip: 12.83 kB
dist/assets/invoke-C2DHGGy9.js                        1.09 kB │ gzip:  0.70 kB
dist/assets/Bubble-C_6U6ZzQ.js                        1.45 kB │ gzip:  0.73 kB
dist/assets/snip-DJ2WJOSt.js                          1.47 kB │ gzip:  0.81 kB
dist/assets/WebviewPanel-Dl9obHmY.js                  3.47 kB │ gzip:  1.81 kB
dist/assets/surface-policy-Dq95qQnR.js                4.83 kB │ gzip:  2.46 kB
dist/assets/proc-D0B7hvBV.js                          7.99 kB │ gzip:  2.84 kB
dist/assets/SchemaPanel-b5u2y6uF.js                   8.72 kB │ gzip:  3.32 kB
dist/assets/panel-D8_N_X2o.js                         8.80 kB │ gzip:  3.81 kB
dist/assets/design-C12P1DZ9.js                       14.24 kB │ gzip:  5.25 kB
dist/assets/InputBar-C7aRUwBr.js                     24.85 kB │ gzip:  8.10 kB
dist/assets/main-DaP3-2y_.js                         31.82 kB │ gzip: 11.87 kB
dist/assets/YbIcon-Du7aSYUJ.js                       31.92 kB │ gzip:  8.87 kB
dist/assets/_plugin-vue_export-helper-DL7yA7ta.js    77.91 kB │ gzip: 30.89 kB
dist/assets/home-DZWUwVTt.js                        128.31 kB │ gzip: 43.35 kB
✓ built in 5.30s
```

Exit code: 0

Command:

```sh
cd /Users/denny/Work/yibao-refactor/desktop && npx vitest run
```

Output:

```text
RUN  v4.1.10 /Users/denny/Work/yibao-refactor/desktop


Test Files  9 passed (9)
     Tests  94 passed (94)
  Start at  19:09:31
  Duration  1.05s (transform 1.68s, setup 0ms, import 2.35s, tests 372ms, environment 1ms)
```

Exit code: 0

Command:

```sh
cd /Users/denny/Work/yibao-refactor/desktop && npx vite build
```

Output:

```text
vite v6.4.3 building for production...
transforming...
✓ 152 modules transformed.
rendering chunks...
computing gzip size...
dist/snip.html                                        0.75 kB │ gzip:  0.42 kB
dist/invoke.html                                      0.91 kB │ gzip:  0.44 kB
dist/design.html                                      1.43 kB │ gzip:  0.62 kB
dist/panel.html                                       1.57 kB │ gzip:  0.65 kB
dist/index.html                                       1.65 kB │ gzip:  0.69 kB
dist/home.html                                        2.01 kB │ gzip:  0.81 kB
dist/assets/logo-CeZ2Ys50.png                        56.13 kB
dist/assets/brain-shell-CZH-UYBJ.png                275.94 kB
dist/assets/WebviewPanel-B7KNpFsN.css                 0.14 kB │ gzip:  0.13 kB
dist/assets/snip-IJI7BwYS.css                         0.66 kB │ gzip:  0.35 kB
dist/assets/invoke-DrrXmXL0.css                       1.10 kB │ gzip:  0.45 kB
dist/assets/surface-policy-BNUeB8WN.css               2.92 kB │ gzip:  0.72 kB
dist/assets/Bubble-YUia62IA.css                       4.02 kB │ gzip:  1.12 kB
dist/assets/InputBar-ClGleGNi.css                     4.89 kB │ gzip:  1.33 kB
dist/assets/YbIcon-DHORsLoG.css                       6.13 kB │ gzip:  1.39 kB
dist/assets/panel-COsRh8Bt.css                        6.51 kB │ gzip:  1.57 kB
dist/assets/SchemaPanel-_3BMFDRB.css                  8.41 kB │ gzip:  1.77 kB
dist/assets/_plugin-vue_export-helper-L5Vw3bEE.css   12.36 kB │ gzip:  2.69 kB
dist/assets/design-j893C5Es.css                      14.49 kB │ gzip:  2.42 kB
dist/assets/main-SWu3xtrs.css                        14.84 kB │ gzip:  3.14 kB
dist/assets/home-Cyq9cw8f.css                        84.28 kB │ gzip: 12.83 kB
dist/assets/invoke-C2DHGGy9.js                        1.09 kB │ gzip:  0.70 kB
dist/assets/Bubble-C_6U6ZzQ.js                        1.45 kB │ gzip:  0.73 kB
dist/assets/snip-DJ2WJOSt.js                          1.47 kB │ gzip:  0.81 kB
dist/assets/WebviewPanel-HZjz_MN_.js                  3.47 kB │ gzip:  1.81 kB
dist/assets/surface-policy-BhlUPrrP.js                4.83 kB │ gzip:  2.45 kB
dist/assets/proc-D6EBeb2F.js                          7.93 kB │ gzip:  2.82 kB
dist/assets/SchemaPanel-b5u2y6uF.js                   8.72 kB │ gzip:  3.32 kB
dist/assets/panel-Boo8zrnd.js                         8.80 kB │ gzip:  3.81 kB
dist/assets/design-C12P1DZ9.js                       14.24 kB │ gzip:  5.25 kB
dist/assets/InputBar-C7aRUwBr.js                     24.85 kB │ gzip:  8.10 kB
dist/assets/main-D8evpXRx.js                         31.78 kB │ gzip: 11.85 kB
dist/assets/YbIcon-Du7aSYUJ.js                       31.92 kB │ gzip:  8.87 kB
dist/assets/_plugin-vue_export-helper-DL7yA7ta.js    77.91 kB │ gzip: 30.89 kB
dist/assets/home-q4nS3uTg.js                        128.31 kB │ gzip: 43.35 kB
✓ built in 6.51s
```

Exit code: 0

Command:

```sh
cd /Users/denny/Work/yibao-refactor/desktop && npx vitest run
```

Output:

```text
 RUN  v4.1.10 /Users/denny/Work/yibao-refactor/desktop


 Test Files  9 passed (9)
      Tests  94 passed (94)
   Start at  18:57:23
   Duration  1.70s (transform 2.44s, setup 0ms, import 4.71s, tests 363ms, environment 1ms)
```

Exit code: 0
