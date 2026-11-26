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

## CDN / S3 / MinIO / R2

`ASSET_STORE=s3` 时，对象存储与 roadmap manifest 解耦，manifest 结构不变（只多一个 `"store": "s3"`）。

`deploy/.env`：

```ini
ASSET_STORE=s3
ASSET_S3_BUCKET=pm-assets
ASSET_S3_REGION=us-east-1
# MinIO / R2 自定义端点；AWS 留空
ASSET_S3_ENDPOINT=
ASSET_S3_PREFIX=
ASSET_S3_ACCESS_KEY=
ASSET_S3_SECRET_KEY=

# 公共 CDN；没有 CDN 时用签名 URL
ASSET_BASE_URL=https://cdn.example.com/assets
ASSET_SIGNED_URLS=0
ASSET_SIGNED_TTL=3600
```

- 容器需要 `boto3`：`docker build --build-arg INSTALL_S3=1 ...`（见 Dockerfile）或在本机 `pip install boto3`；
- 上传 key 为 `<prefix>/<sha256>`，天然内容寻址，重复内容 `head_object` 命中后直接复用；
- 有 `ASSET_BASE_URL` 时，API 返回 CDN URL；私有桶设置 `ASSET_SIGNED_URLS=1`，API 返回带 `ASSET_SIGNED_TTL` 的签名 URL；
- `GET/HEAD /api/assets/<id>` 对 s3 返回 `302` 到 CDN/签名 URL，下载行为与 fs 一致；
- `pm asset check` 对 s3 使用 `head_object` 校验对象存在。


## 校验

```bash
# manifest 与 ASSET_DIR 文件是否一致（缺失/大小不符）
pm asset check

# 或手工核对
sha256sum /vol1/1000/pm-assets/<pid>/<ab>/<sha256>*
cat projects/<pid>/assets.json
```

`pm asset check` 检查 manifest 中每个 `sha256` 是否存在于 `ASSET_DIR`，并核对大小；缺失时退出码非 0。


- 上传/下载都走 pm token；
- 限制 `ASSET_MAX_MB`，校验 sha256/长度；
- `ASSET_DIR` 不要放进 Git；`.gitignore` 已包含 `assets/`；
- SMB 只开局域网；公网走 HTTPS/CDN。
