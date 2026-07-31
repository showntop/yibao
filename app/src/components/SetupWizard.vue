<script setup lang="ts">
import { ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import YbIcon from "./YbIcon.vue";

// 首启设置向导：没配 LLM key 时大脑不会启动（Rust 侧 setup-config-needed 事件触发）。
// 保存写数据目录 .env，随后 Rust 拉起大脑；主界面靠大脑上线事件自然接管。
const props = defineProps<{ model: string; baseUrl: string; voice: string }>();
const emit = defineEmits<{ saved: [] }>();

const key = ref("");
const model = ref(props.model);
const baseUrl = ref(props.baseUrl);
const voice = ref(props.voice);
const saving = ref(false);
const err = ref("");

// edge-tts 常用中文音色
const VOICES: [string, string][] = [
  ["zh-CN-XiaoxiaoNeural", "晓晓（女声·活泼）"],
  ["zh-CN-XiaoyiNeural", "晓伊（女声·温柔）"],
  ["zh-CN-YunxiNeural", "云希（男声·清亮）"],
  ["zh-CN-YunjianNeural", "云健（男声·沉稳）"],
];

async function save() {
  if (!key.value.trim()) {
    err.value = "请填入 API Key";
    return;
  }
  saving.value = true;
  err.value = "";
  try {
    await invoke("save_setup_config", {
      key: key.value,
      model: model.value,
      baseUrl: baseUrl.value,
      voice: voice.value,
    });
    emit("saved");
  } catch (e) {
    err.value = String(e);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="wizard">
    <div class="title"><YbIcon class="welcome-ic" name="wave" :size="18" /> 欢迎使用译宝</div>
    <p class="intro">译宝靠大模型思考，第一次用先填一下 API Key（存在本机，不上传）。</p>

    <label class="field">
      <span class="label">API Key <i class="req">*</i></span>
      <input v-model="key" type="password" placeholder="智谱 / DeepSeek 等 OpenAI 兼容端点的 key" @keydown.enter="save" />
    </label>
    <label class="field">
      <span class="label">模型</span>
      <input v-model="model" placeholder="glm-4.6" />
    </label>
    <label class="field">
      <span class="label">Base URL</span>
      <input v-model="baseUrl" placeholder="留空 = 智谱官方端点" />
    </label>
    <label class="field">
      <span class="label">声音</span>
      <select v-model="voice">
        <option v-for="[v, label] in VOICES" :key="v" :value="v">{{ label }}</option>
      </select>
    </label>

    <div v-if="err" class="err"><YbIcon name="alert" :size="14" />{{ err }}</div>
    <button class="save" :disabled="saving" @click="save">{{ saving ? "保存并启动中…" : "保存并启动" }}</button>
    <p class="hint">也可稍后手改配置：~/Library/Application Support/yibao/.env</p>
  </div>
</template>

<style scoped>
.wizard {
  padding: var(--yb-space-3) 14px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-glass-border);
  box-shadow: var(--yb-shadow);
  font-size: var(--yb-fs-lg);
  color: var(--yb-text);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.title {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  font-weight: var(--yb-fw-bold);
  font-size: var(--yb-fs-lg);
}
.welcome-ic {
  color: var(--yb-accent);
}
.intro {
  margin: 0;
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-base);
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.label {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.req {
  color: var(--yb-accent);
  font-style: normal;
}
input,
select {
  padding: 7px 10px;
  border-radius: var(--yb-radius-sm);
  border: 1px solid var(--yb-surface-border);
  background: var(--yb-bg);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  outline: none;
}
input:focus,
select:focus {
  border-color: var(--yb-accent);
}
.err {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
.save {
  padding: 8px 0;
  border-radius: var(--yb-radius-sm);
  border: none;
  cursor: pointer;
  background: var(--yb-accent);
  color: #fff;
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  transition: filter var(--yb-dur-fast);
}
.save:hover:not(:disabled) {
  filter: brightness(0.96);
}
.save:disabled {
  opacity: 0.6;
  cursor: default;
}
.hint {
  margin: 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
</style>
