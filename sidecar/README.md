# 译宝 · Python 大脑核心（sidecar）

## 安装
```bash
cd sidecar
uv sync --extra dev            # 开发（含 pytest）
uv sync --extra memory         # 可选：启用 mem0 真实记忆（较重）
```

## 测试
```bash
uv run pytest -q
```

## 运行 CLI（文本壳）
```bash
# 任意 OpenAI 兼容 provider（智谱 GLM / DeepSeek / OpenAI …），见 .env.example
export YIBAO_LLM_API_KEY=... YIBAO_LLM_MODEL=deepseek-chat YIBAO_LLM_BASE_URL=https://api.deepseek.com
uv run yibao-brain              # 真实 LLM（需 key）
uv run yibao-brain --fake       # 假模型，无需 key/联网
```

## 配置
环境变量见 `.env.example`。

## 作为 sidecar（供桌面壳调用）
`uv run yibao-brain-server` —— 行分隔 JSON over stdio；协议见 `docs/superpowers/plans/2026-07-16-yibao-v1-plan2-shell-and-ipc.md`。
