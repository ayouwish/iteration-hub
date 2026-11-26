# Asset Store：通用资源服务

多媒体/大文件不进 Git，由 pm 的 Asset Store 统一管理。
NAS/SMB 只是其中一种 `fs` 后端；换成对象存储或 CDN 时，roadmap 和客户端都不变。

## 模型

```
client / DSH
     │  HTTP /api/assets
     ▼
 Asset Store (pm.py)
     │ provider
     ├── fs  → ASSET_DIR（NAS 共享 / 本地盘 / 容器 /assets）
     ├── s3  → MinIO / R2 / S3（后续）
     └── http→ 只读 CDN（ASSET_BASE_URL）
```

- **内容寻址**：id = sha256，不可变，天然适合 CDN 缓存；
- roadmap 只存 `projects/<pid>/assets.json`（文本），二进制不进 Git；
- 客户端不需要挂载 SMB，走 HTTP 上传/下载；局域网也可以继续用 SMB。

## 配置

`deploy/.env`：

```ini
# fs provider 的根（compose 会挂到容器 /assets）
ASSETS_DIR=/vol1/1000/pm-assets

# 可选：对外 CDN/Nginx/R2 基地址；留空则用 /api/assets/<id>
ASSET_BASE_URL=

# 可选：单文件上限
ASSET_MAX_MB=50
```

compose 会设置：

```yaml
environment:
  ASSET_DIR: /assets
  ASSET_BASE_URL: "${ASSET_BASE_URL:-}"
  ASSET_MAX_MB: "${ASSET_MAX_MB:-50}"
volumes:
  - "${ASSETS_DIR}:/assets"
```

## API

```text
POST /api/assets?project=<pid>&feature=<fid>&name=<filename>
     Authorization: Bearer <token>
     Content-Type: <mime>
     body: 二进制
     → {ok, id, sha256, name, feature, mime, size, url}

GET  /api/assets?project=<pid>&feature=<fid>     # 列表
GET  /api/assets/<id>                            # 下载
HEAD /api/assets/<id>                            # 存在性/长度
```

示例：

```bash
curl -X POST "http://192.168.50.104:8787/api/assets?project=iteration-hub&feature=F-2026-11-01&name=demo.mp4" \
  -H "Authorization: Bearer $PM_TOKEN" \
  -H "Content-Type: video/mp4" \
  --data-binary @demo.mp4
```

重复内容会复用同一个 `sha256`，不会重复存储。

## CLI

```bash
pm asset add --file demo.mp4 --feature F-2026-11-01
pm asset list --feature F-2026-11-01
pm asset url --id <sha256>
```

`pm asset add` 会把文件写入 `ASSET_DIR` 并更新 `assets.json`；二进制目录被 `.gitignore` 排除，`pm sync` 只提交 manifest。

## manifest

`projects/<pid>/assets.json`：

```json
{
  "store": "fs",
  "files": [
    {
      "id": "sha256:…",
      "sha256": "…",
      "name": "demo.mp4",
      "feature": "F-2026-11-01",
      "mime": "video/mp4",
      "size": 12345678,
      "created_at": "2026-11-27T00:00:00Z",
      "url": "/api/assets/…"
    }
  ]
}
```

## fs 后端：fnOS / NAS 共享

`ASSETS_DIR` 指向 NAS 上的共享目录，例如 `/vol1/1000/pm-assets`：

1. fnOS 控制台 → 共享文件夹 → 新建 `pm-assets`；
2. 给 NAS 用户（如 `drinkwish`）读写权限；
3. 启用 SMB；
4. compose 把 `/vol1/1000/pm-assets` 挂到容器 `/assets`；
5. PUID/PGID 对齐后，容器和 SMB 用户都能读写。

客户端如果只想直接拖文件，也可以挂 SMB：

- Windows：`\\192.168.50.104\pm-assets` 或 `net use Z: ...`
- WSL：`/mnt/z` 或 `drvfs`/`cifs` 挂载
- macOS：`smb://192.168.50.104/pm-assets`
- Linux：`mount -t cifs ...`

SMB 只是 fs 后端的局域网入口；HTTP API 才是通用能力。

## CDN / 对象存储

- 把 `ASSET_BASE_URL` 指向 Nginx/Caddy/R2 公共域名，API 返回的 `url` 自动变成 CDN 地址；
- 内容寻址 + 长缓存：`Cache-Control: public, max-age=31536000, immutable`；
- 后续加 `ASSET_STORE=s3`（MinIO/R2），roadmap 和客户端不变；
- 私有资源用签名 URL（后续阶段）。

## 安全

- 上传/下载都走 pm token；
- 限制 `ASSET_MAX_MB`，校验 sha256/长度；
- `ASSET_DIR` 不要放进 Git；`.gitignore` 已包含 `assets/`；
- SMB 只开局域网；公网走 HTTPS/CDN。
