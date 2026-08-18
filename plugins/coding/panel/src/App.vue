<script setup lang="ts">
// coding:studio(R4 阶段三 T4 过渡壳):单 StationView 验证搬运无损;T6 重写为多工位壳。
import StationView from "./components/StationView.vue";
</script>

<template>
  <StationView :focused="true" :autoplay="true" default-cwd="" />
</template>
