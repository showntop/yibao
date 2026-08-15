<script setup lang="ts">
import { onMounted } from "vue";
import { App as CapApp } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";
import { useRouter } from "vue-router";
import { handlePairUrl } from "./deeplink";
import { saveConn } from "./api/connection";

const router = useRouter();
onMounted(() => {
  // 浏览器 web 平台 @capacitor/app 无 appUrlOpen 实现（可能抛错），只挂真机；
  // 真机上扫桌面二维码 → yibao://pair → 自动配对并跳转 chat
  if (!Capacitor.isNativePlatform()) return;
  void CapApp.addListener("appUrlOpen", ({ url }) => {
    void handlePairUrl(url, { save: saveConn, push: (to) => router.replace(to) });
  });
});
</script>

<template>
  <router-view />
</template>
