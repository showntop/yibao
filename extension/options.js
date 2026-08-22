import { DEFAULT_PORT, getConfig, yibaoFetch } from "./shared.js";

// el：getElementById 简写（DOM 定位语义化；取代单字母 $）
const el = (id) => document.getElementById(id);
const msg = (t) => (el("msg").textContent = t);

const config = await getConfig();
el("token").value = config.token;
el("port").value = config.port;

el("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    token: el("token").value.trim(),
    port: Number(el("port").value) || DEFAULT_PORT,
  });
  msg("已保存");
});

el("test").addEventListener("click", async () => {
  const token = el("token").value.trim();
  const port = Number(el("port").value) || DEFAULT_PORT;
  try {
    const j = await yibaoFetch("/health", { token, port });
    msg(j.ok ? "✓ 连接成功，译宝大脑在线" : `✗ ${j.error || "连接失败"}`);
  } catch {
    msg(`✗ 连不上 127.0.0.1:${port}——译宝在运行吗？端口对吗？`);
  }
});
