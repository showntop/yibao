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
export YIBAO_GLM_API_KEY=...
uv run yibao-brain              # 真实 GLM（需 key）
uv run yibao-brain --fake       # 假模型，无需 key/联网
```

## 配置
环境变量见 `.env.example`。
