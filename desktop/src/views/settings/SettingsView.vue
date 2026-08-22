<script setup lang="ts">
// 设置页（home 大窗唯一内容）：容器 = 左侧分类目录 + 右侧分类 section（自包含子组件）。
// 各分类的状态/加载逻辑已拆至 components/settings/*Section.vue；共享样式在 assets/settings.css。
import { ref } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import GeneralSection from "./GeneralSection.vue";
import ProactiveSection from "./ProactiveSection.vue";
import PrivacySection from "./PrivacySection.vue";
import AboutSection from "./AboutSection.vue";

type Cat = "general" | "proactive" | "privacy" | "about";
const cat = ref<Cat>("general");

const CATS: { id: Cat; label: string; icon: "gear" | "sparkle" | "lock" | "info" }[] = [
  { id: "general", label: "通用", icon: "gear" },
  { id: "proactive", label: "主动协助", icon: "sparkle" },
  { id: "privacy", label: "隐私与权限", icon: "lock" },
  { id: "about", label: "关于", icon: "info" },
];
</script>

<template>
  <!-- 设置页：macOS 系统设置（Ventura+）语言——左侧分类目录 + 右侧分组卡片。
       原 11 个分组平铺一列滚不到底，现按语义收成 4 类；「感知日志」「记忆管理」
       这两个本质是数据浏览器的长列表，各自独占分类内的卡，不再挤在开关中间。 -->
  <div class="settings">
    <!-- 分类目录：与 Home 侧栏区分（这是二级导航，用文字列表不用图标底） -->
    <nav class="cat-nav">
      <h1 class="cat-title" data-tauri-drag-region>设置</h1>
      <button
        v-for="c in CATS"
        :key="c.id"
        class="cat-item"
        :class="{ on: cat === c.id }"
        @click="cat = c.id"
      >
        <YbIcon class="cat-ic" :name="c.icon" :size="14" />
        <span>{{ c.label }}</span>
      </button>
    </nav>

    <div class="s-scroll">
      <GeneralSection v-if="cat === 'general'" />
      <ProactiveSection v-else-if="cat === 'proactive'" />
      <PrivacySection v-else-if="cat === 'privacy'" />
      <AboutSection v-else-if="cat === 'about'" />
    </div>
  </div>
</template>
