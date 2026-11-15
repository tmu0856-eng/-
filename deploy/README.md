# 独立评审演示部署

本地开发服务仍为 8765；评审服务使用独立数据目录 `data/demo-public` 和端口 8767。不要把未公开论文、个人数据或完整工作目录直接暴露到公网。

当前线上采用 [小内存服务器独立 Docker 部署](CLOUD_DOCKER.md)，应用和评审数据均在服务器上，电脑关机不影响网站。以下两种方案保留作为其他资源条件下的选择。

## 方案一：现有 Windows 电脑 + ngrok

适合先验证公网访问，不需购买服务器。需要用户自己的 ngrok 账号和授权令牌；程序运行、电脑联网和不休眠是持续可访问的前提。

```powershell
python scripts/prepare_demo.py
python scripts/run_demo.py
```

账号密码保存在被 Git 忽略的 `.local/reviewer-access.txt`，服务商密钥仅在 `.env.demo`。第一次生成不会覆盖已有文件。先在 `http://127.0.0.1:8767` 验证评审登录，导入允许公开演示的论文，等待 BGE 索引完成。

安装官方 ngrok 客户端并按其账号页面配置令牌后，将 `.env.demo` 中 `DEMO_COOKIE_SECURE` 改为 `1`，重启评审服务，再运行：

```powershell
ngrok http 8767
```

用返回的 HTTPS 地址测试登录、PDF 页图、直接问答和 Agent SSE，确认后再填写演示网址；启用 Secure Cookie 后请通过 HTTPS 访问。初次浏览可能出现 ngrok 提示页。

ngrok 免费方案目前有 1 GB/月流量、20,000 次/月请求限制；具体以 [官方限制](https://ngrok.com/docs/pricing-limits/free-plan-limits) 和账号页面为准。PDF 图片可能很快消耗流量，不能把这个方案描述成不受限的长期托管。保存配置不代表已经发布网址。

Cloudflare Quick Tunnel 的官方说明列出不支持 SSE，不能直接用于本项目的流式 Agent。参见 [官方说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。

## 方案二：独立 Linux 主机 + Docker

用于持续评审时更稳妥；需用户选定服务器并承担资源费用。建议从 8 核 CPU、16 GB 内存、40 GB 可用磁盘做小规模单人演示测试；这是部署起点，尚未实测该云规格的双模型延迟，不是性能保证。GPU 可选。CPU 模式默认精排 20 个候选以控制延迟。

准备 `.env.demo`、下载两份官方模型到 `.local/models` 后执行：

```bash
docker compose -f compose.demo.yaml up -d --build
```

Compose 只监听本机 8767；需配置 HTTPS 反向代理或有稳定域名的隧道，再设置 Secure Cookie。反向代理必须转发 Host/协议头、关闭 SSE 缓冲，并允许较长读取超时。示例 Nginx location：

```nginx
location / {
    proxy_pass http://127.0.0.1:8767;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

模型目录只读挂载；论文库写入 `data/demo-public`。镜像不包含模型权重、论文、密钥、Git 历史或开发计划。`LOCAL_MODELS=1` 安装 CPU Torch 和本地模型依赖；基础镜像默认不含这部分。首次构建及模型下载需较大网络流量。

## 验证与维护

- `/api/health` 允许匿名健康检查；论文、问答、模型状态等接口要求登录。登录 Cookie 为 HttpOnly、SameSite=Strict，8 小时过期。
- 默认每 IP 每分钟最多 5 次登录尝试、10 次 POST 操作，全文翻译最多 20 页，论文上限 80 篇。限制用于小规模演示，不代表付费接口有总费用封顶；请同时在模型服务商设置额度。
- 定期使用 SQLite 在线备份接口保存数据库，并备份 `files/`；运行期间不要只复制单个 SQLite 主文件而遗漏 WAL。
- 当前独立 Docker 部署已验证公网登录、SSE、扫描解析和容器重启恢复；长期流量、调用额度及持续在线需在使用中维护。本文的备用方案不自动创建账号或购买资源。
