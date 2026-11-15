# 小内存服务器独立部署

应用运行在服务器 Docker 容器中，使用 `compose.cloud.yaml`。MiMo 通过远程接口提供回答、翻译和视觉转录；服务器不安装本地 BGE-M3 和 reranker 权重。

## 运行配置

将私密配置保存在服务器项目目录的 `.env.cloud`，设置回答接口、评审密码和会话密钥。没有兼容的向量接口时，`EMBEDDING_MODEL` 和 `RERANKER_MODEL` 留空，检索使用 BM25 与 TF-IDF。

可从 [云端配置示例](cloud.env.example) 复制配置；示例不包含实际密钥或密码。

已有文献向量随独立评审数据库保存，不删除、不重新计算；配置页显示保留数量。缓存存在不代表语义检索已经启用。接入远程向量服务前，需核对模型、维度、池化及索引空间兼容性；仅使用相同维数的其他模型无法复用这些向量。

配置与数据分别放置：

```text
/opt/researchdock/
  compose.cloud.yaml
  .env.cloud                  私密配置，权限 600
  data/cloud-public/          独立评审库、PDF 和已有向量
```

镜像仅包含程序和运行依赖；不包含密钥、论文、向量数据库或模型权重。数据与私密配置通过服务器管理通道单独迁移，不进入 Git 仓库。

## 启动与维护

在服务器装载镜像并标记为 `researchdock:cloud` 后运行：

```bash
docker compose -p researchdock-cloud -f compose.cloud.yaml up -d
docker compose -p researchdock-cloud -f compose.cloud.yaml ps
docker compose -p researchdock-cloud -f compose.cloud.yaml logs --tail=80
```

容器仅绑定服务器回环地址 `127.0.0.1:8767`，由 Nginx 代理 HTTPS。反向代理需转发 Host、协议和客户端地址，关闭 SSE 缓冲，读取超时至少 300 秒。无需开放 8765 或 8767 的公网端口。

桥接网络中的应用应只信任宿主机网关发来的代理头。在 `.env.cloud` 设置 `FORWARDED_ALLOW_IPS=127.0.0.1,网关地址`；网关可通过 `docker network inspect researchdock-cloud_default` 查看。网络重建后需重新核对，避免 HTTPS 登录被同源校验拒绝。

当前配置限制容器内存为 640 MB，总内存与交换额度为 1536 MB，并限制日志大小、进程数和计算线程。1 GB 主机需准备交换空间，并在实际文献与并发场景下观察内存；交换空间不能替代内存性能。

容器使用 `unless-stopped` 重启策略，Docker 服务应随服务器启动。停止与启动分别使用：

```bash
docker compose -p researchdock-cloud -f compose.cloud.yaml stop
docker compose -p researchdock-cloud -f compose.cloud.yaml start
```

HTTPS 证书需配置自动续期，并在续期成功后重新加载 Nginx。备份时使用 SQLite 在线备份接口，同时保存 `files/`。向量缓存和原文均位于挂载的数据目录，不随容器重建丢失。
