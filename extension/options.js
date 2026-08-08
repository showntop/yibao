import { DEFAULT_PORT, getConfig } from "./shared.js";

const $ = (id) => document.getElementById(id);
const msg = (t) => ($("msg").textContent = t);

const cfg = await getConfig();
$("token").value = cfg.token;
$("port").value = cfg.port;

$("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    token: $("token").value.trim(),
    port: Number($("port").value) || DEFAULT_PORT,
  });
  msg("已保存");
});

$("test").addEventListener("click", async () => {
  const token = $("token").value.trim();
  const port = Number($("port").value) || DEFAULT_PORT;
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/health`, { headers: { "X-Yibao-Token": token } });
    const j = await resp.json();
    msg(j.ok ? "✓ 连接成功，译宝大脑在线" : `✗ ${j.error || "连接失败"}`);
  } catch {
    msg(`✗ 连不上 127.0.0.1:${port}——译宝在运行吗？端口对吗？`);
  }
});
