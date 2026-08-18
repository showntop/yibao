# Phase 0 · 止血实施计划（2026-08-13）

> **Goal:** 让 main 重新可打包、可自动验证——为主线各阶段的真机验收扫清前置障碍。
>
> **关联 spec：** `docs/superpowers/specs/2026-08-13-pet-os-mainline-design.md` §3 Phase 0。
>
> **范围：** 只做两件事——修断掉的打包链（Task 1）、建自动闸门（Task 2）。仓库清理已延后到文末附录，不阻塞验收；`feat/coding-r2` 走单独计划，本计划完全不碰。
>
> **预估：** 两三小时。Task 1 是唯一有代码改动的任务。

## 背景

`npm run tauri build` 走 `tauri.conf.json:9` 的 `beforeBuildCommand: npm run build`，而 `npm run build` = `vue-tsc --noEmit && vite build`。当前 `vue-tsc` 报 3 个错，**release 打包链断了**，自 `d43c856`（2026-08-09，联网搜索多通道）起潜伏 25 个提交无人发现。

根因不是这三个错本身，而是**仓库零 CI**（无 `.github/`）：900 条 pytest、59 条 vitest、cargo check 全靠本地手跑，漏一次就潜伏到下次手动打包才暴露。

## Global Constraints

- 每任务一 commit，中文 scope
- Task 1 完成后必须验证 `npm run build` exit 0，并真正跑通一次 `npm run tauri -- build --debug` 出 `.app`
- CI 只跑既有验证命令，**不新增 lint 规则**（引入 ESLint 是独立决策，不在本计划）

---

## Task 1: 修复 vue-tsc 类型错误

**Files:** Modify `app/src/components/SettingsView.vue`

**根因：** `KEY_PROVIDERS: SearchProvider[]`（:150）的 `.includes()` 返回 `boolean`，不产生类型收窄；模板 :658 用它做 `v-if` 后，:664-666 仍以完整的 6 值联合 `SearchProvider` 去索引只有 3 个键的 `searchKeys`（:153）。

**方案：** 不依赖模板类型收窄（脆弱），改用显式访问器函数。

- [ ] **Step 1: 加窄类型与访问器**

`SettingsView.vue` 的 :150 附近改为：

```ts
type KeyProvider = "brave" | "tavily" | "serper";
const KEY_PROVIDERS: KeyProvider[] = ["brave", "tavily", "serper"];

/** 6 值联合收窄到「需要 key 的 3 个」；不是则 null。模板与逻辑共用，避免依赖模板收窄。 */
function asKeyProvider(p: SearchProvider): KeyProvider | null {
  return (KEY_PROVIDERS as readonly SearchProvider[]).includes(p) ? (p as KeyProvider) : null;
}

function searchKeyOf(p: SearchProvider): string {
  const k = asKeyProvider(p);
  return k ? searchKeys.value[k] : "";
}

function updateSearchKey(p: SearchProvider, v: string): void {
  const k = asKeyProvider(p);
  if (k) void setSearchKey(k, v);
}
```

（`setSearchKey`（:180）签名已是 `"brave" | "tavily" | "serper"`，无需改动。）

- [ ] **Step 2: 模板改用访问器**

:658-666 三处：

```html
          <template v-if="asKeyProvider(searchProvider)">
            <div class="s-note">API key 此处填写优先于 .env（YIBAO_SEARCH_{{ searchProvider.toUpperCase() }}_KEY）；留空 = 用 .env 配置。</div>
            <label class="s-field">
              <span class="s-label">{{ searchProvider === "brave" ? "Brave" : searchProvider === "tavily" ? "Tavily" : "Serper" }} API Key</span>
              <input
                type="password"
                :value="searchKeyOf(searchProvider)"
                :placeholder="searchKeyOf(searchProvider) ? '已配置（输入以更换）' : '未配置'"
                @change="updateSearchKey(searchProvider, ($event.target as HTMLInputElement).value)"
              />
            </label>
          </template>
```

- [ ] **Step 3: 验证**

```bash
cd app && npx vue-tsc --noEmit && npm run build
```
Expected: 两条均 exit 0，`vue-tsc` 零输出

- [ ] **Step 4: 真机打包验证（本任务的真正验收）**

```bash
cd app && npm run tauri -- build --debug
```
Expected: 产出 `src-tauri/target/debug/bundle/macos/译宝.app`

- [ ] **Step 5: Commit**

```bash
git add app/src/components/SettingsView.vue
git commit -m "fix(settings): 搜索 key 类型收窄——修 vue-tsc 三错，恢复 release 打包链"
```

---

## Task 2: 建 CI

**Files:** Create `.github/workflows/ci.yml`

**设计：** 全部跑 macos-latest（app 本就 macOS-only，Tauri 的 Rust 依赖在 ubuntu 上还要装 webkit2gtk，得不偿失）。三个 job 并行，互不阻塞，便于快速定位。

- [ ] **Step 1: 写 workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  sidecar:
    name: sidecar pytest
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --extra dev
        working-directory: sidecar
      - run: uv run pytest -q
        working-directory: sidecar

  frontend:
    name: vue-tsc + vitest
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: app/package-lock.json
      - run: npm ci
        working-directory: app
      - run: npx vue-tsc --noEmit
        working-directory: app
      - run: npx vite build
        working-directory: app
      - run: npm test
        working-directory: app

  rust:
    name: cargo check + test
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
        with:
          workspaces: app/src-tauri
      # tauri.conf.json 的 bundle.resources 引用 resources/bin/uv，缺失会让配置校验失败
      - run: ./scripts/prepare-dist.sh
        working-directory: app
      - run: cargo check --manifest-path app/src-tauri/Cargo.toml
      - run: cargo test --manifest-path app/src-tauri/Cargo.toml
```

- [ ] **Step 2: 本地预演三个 job 的命令**

逐条在本地跑一遍确认可复现（尤其 `prepare-dist.sh` 在干净环境的幂等性）。若 `npm ci` 因 `package-lock.json` 缺失失败，先补提交 lock 文件。

- [ ] **Step 3: Commit + 推送验证**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: 建 GitHub Actions——pytest/vue-tsc/vitest/cargo 四道闸门"
```

推后到 GitHub 上确认三个 job 全绿。**若 job 因环境问题红，修到绿为止**——一个默认红的 CI 比没有 CI 更糟。

- [ ] **Step 4: 开启分支保护**

在 GitHub 仓库设置里把三个 job 设为 main 的 required status checks。

---

## Task 3: 验收

- [ ] **Step 1: 自动化全绿**

```bash
cd sidecar && uv run pytest -q
cd ../app && npx vue-tsc --noEmit && npx vite build && npm test
cargo check --manifest-path src-tauri/Cargo.toml
```

- [ ] **Step 2: CI 在 GitHub 上三 job 全绿**

- [ ] **Step 3: `npm run tauri -- build --debug` 出 `.app` 且能启动**

- [ ] **Step 4: 在主线 spec 追加实装记录**

在 `docs/superpowers/specs/2026-08-13-pet-os-mainline-design.md` 末尾加「Phase 0 实装记录」：修复的类型错误、CI 三 job 构成。

---

## 附：仓库清理（延后执行，不阻塞本计划验收）

> **状态：延后。** 与止血无关——分支和 worktree 留着不影响打包、CI 或任何验收。等主线推进到合适的节点再做，或并入其他计划。
>
> 记在这里只是为了不丢掉已完成的判断（下列分支的可删结论已用 `git merge-base --is-ancestor <branch> main` 验证过，`main..<branch>` 均为空，即工作全在 main 上了）。

- [ ] **删已合并分支**

```bash
git branch -d codex/plugin-app-os-shell ui-polish visual-polish feature/rust-session
```

（用 `-d` 不用 `-D`：git 会二次校验确已合并，删不掉说明判断有误，停下来查。）

- [ ] **处理 `feature/session-state`**

唯一 commit 是 `ab4b064 tmp`，其引入的 `app/src/state/domains/*` 在 main 上已有并行实现（`3b831e6` 修复了 rebase 丢文件问题）。确认无独有价值后：

```bash
git branch -D feature/session-state
```

- [ ] **清 worktree**

```bash
git worktree remove /Users/denny/Work/yibao-ui
git worktree remove /Users/denny/Work/yibao-visual
git worktree remove .worktrees/plugin-app-os-shell
git worktree prune
```

> **不动 `feat/coding-r2` 及其 worktree `.worktrees/feat-coding-r2`。** 该分支由单独计划管理，本计划不评估、不 rebase、不合并、不删除。执行后除 main 外应只剩 `feat/coding-r2`。
