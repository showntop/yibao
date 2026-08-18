<script setup>
// coding:studio 骨架(R4 阶段一):验证 module 面板全链路——
// 桥注入(window.yibao / YIBAO_BRIDGE_VERSION)、init 数据、invoke 往返(coding.list)。
// 阶段二起此处替换为真正的多工位 UI。
import { ref } from "vue";

const bridgeVer = window.YIBAO_BRIDGE_VERSION ?? "(未注入)";
const init = ref(null);
const result = ref("");
const error = ref("");

window.yibao.onInit((d) => { init.value = d; });

async function ping() {
  error.value = "";
  result.value = "";
  try {
    const r = await window.yibao.invoke("coding.list", {});
    result.value = JSON.stringify(r, null, 2).slice(0, 800);
  } catch (e) {
    error.value = String(e);
  }
}
</script>

<template>
  <main class="hello">
    <h1>coding:studio(骨架)</h1>
    <p>桥版本:{{ bridgeVer }} ｜ init 数据:{{ init ? "已收到" : "等待中…" }}</p>
    <button @click="ping">调 coding.list 验证往返</button>
    <pre v-if="result">{{ result }}</pre>
    <p v-if="error" class="err">{{ error }}</p>
  </main>
</template>
