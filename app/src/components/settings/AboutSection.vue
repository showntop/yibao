<script setup lang="ts">
// 设置页「关于」分类：版本 / 数据目录（只读元信息，自包含）。
import { onMounted, ref } from "vue";
import { getVersion } from "@tauri-apps/api/app";

const version = ref("…");
onMounted(async () => {
  try {
    version.value = await getVersion();
  } catch { /* 保留占位 */ }
});
</script>

<template>
  <section class="s-group">
    <div class="s-group-title">译宝</div>
    <div class="s-row">
      <span class="s-row-label">版本</span>
      <span class="s-row-value">v{{ version }}</span>
    </div>
    <div class="s-row">
      <span class="s-row-label">数据目录</span>
      <span class="s-row-value">~/Library/Application Support/com.yibao.desktop/</span>
    </div>
  </section>
</template>
