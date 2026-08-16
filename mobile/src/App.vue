<script setup lang="ts">
import { computed, onMounted } from "vue";
import { App as CapApp } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";
import { useRoute, useRouter } from "vue-router";
import { handleDeepUrl } from "./deeplink";
import { saveConn } from "./api/connection";
import TabBar from "./components/TabBar.vue";

const router = useRouter();
const route = useRoute();
// 底部导航常驻（M2）：配对页隐藏——没连上大脑前四 Tab 哪儿都进不去
const showTabBar = computed(() => route.path !== "/pairing");

onMounted(() => {
  // 浏览器 web 平台 @capacitor/app 无 appUrlOpen 实现（可能抛错），只挂真机；
  // 真机上扫桌面二维码 → yibao://pair → 自动配对并跳转 chat；
  // 推送深链 → yibao://approvals → 直达审批页（P4 推送落地后即用）
  if (!Capacitor.isNativePlatform()) return;
  void CapApp.addListener("appUrlOpen", ({ url }) => {
    void handleDeepUrl(url, { save: saveConn, push: (to) => router.replace(to) });
  });
});
</script>

<template>
  <router-view />
  <TabBar v-if="showTabBar" />
</template>
