<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { normalizeHost, saveConn, testConn, type ConnConfig } from "../api/connection";

const router = useRouter();
const host = ref(new URLSearchParams(location.search).get("host") ?? "");
const token = ref(new URLSearchParams(location.search).get("token") ?? "");
const testing = ref(false);
const result = ref("");

async function onTest() {
  testing.value = true;
  result.value = "";
  const r = await testConn({ host: normalizeHost(host.value), token: token.value });
  result.value = r.ok ? "✓ 连接成功" : `✗ ${r.reason}`;
  testing.value = false;
}

async function onSave() {
  const c: ConnConfig = { host: normalizeHost(host.value), token: token.value.trim() };
  const r = await testConn(c);
  if (!r.ok) {
    result.value = `✗ ${r.reason}`;
    return;
  }
  await saveConn(c);
  router.replace("/chat");
}
</script>

<template>
  <main class="pair">
    <h2>连接译宝</h2>
    <p class="hint">服务器地址与 token 在桌面端「设置 → 手机伴生端」获取（P5 提供；开发期从 settings.json 的 http.mobile_token 取）。</p>
    <label>服务器地址<input v-model="host" placeholder="http://127.0.0.1:19527" inputmode="url" /></label>
    <label>Token<input v-model="token" placeholder="http.mobile_token" autocapitalize="off" /></label>
    <div class="row">
      <button :disabled="testing || !host || !token" @click="onTest">测试连接</button>
      <button class="primary" :disabled="!host || !token" @click="onSave">保存并进入</button>
    </div>
    <p v-if="result" class="result">{{ result }}</p>
  </main>
</template>

<style scoped>
.pair { padding: 24px 16px; display: flex; flex-direction: column; gap: 14px; }
.hint { font-size: 13px; opacity: 0.65; }
label { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
input { padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size: 15px; background: transparent; color: inherit; }
.row { display: flex; gap: 10px; }
button { flex: 1; padding: 12px; border-radius: 10px; border: 1px solid #ccc; background: transparent; color: inherit; font-size: 15px; }
button.primary { background: #2f6fed; border-color: #2f6fed; color: #fff; }
.result { font-size: 14px; }
</style>
