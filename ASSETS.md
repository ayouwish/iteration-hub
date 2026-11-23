# 多媒体资源：走 NAS 共享，不走 Git

原则：

- **文本/配置**（feature JSON、spec、design tokens）→ roadmap git；
- **截图/小图** → 压缩后可进 git；
- **视频、原始截图、APK/IPA、构建产物** → NAS 共享目录，roadmap 只存 manifest。

## 1. 在 fnOS 上建共享

1. 打开 fnOS 控制台 → **共享文件夹** → 新建，例如 `pm-assets`；
2. 权限给 NAS 用户 `drinkwish`（或你实际使用的用户）**读写**；
3. 打开 **文件服务 / SMB**，确认 SMB 已启用；
4. 确认共享在局域网可访问：

```
\\192.168.50.104\pm-assets
```

5. 记下 fnOS 上的实际路径，例如：

```
/vol1/1000/pm-assets
```

把它填进 `deploy/.env`：

```ini
ASSETS_DIR=/vol1/1000/pm-assets
```

compose 会把它挂到容器的 `/assets`，并保证 PUID/PGID 可读写。

## 2. 客户端挂载

### Windows

```powershell
net use Z: \\192.168.50.104\pm-assets /user:drinkwish *
```

或在资源管理器地址栏输入 `\\192.168.50.104\pm-assets`，映射为 `Z:`。

### WSL

优先复用 Windows 已映射的盘：

```
/mnt/z
```

如果没映射，用 drvfs 挂载：

```bash
sudo mkdir -p /mnt/pm-assets
sudo mount -t drvfs '\\192.168.50.104\pm-assets' /mnt/pm-assets
```

或用 CIFS（需要 cifs-utils；WSL 内核支持时可用）：

```bash
sudo apt install cifs-utils
sudo mkdir -p /mnt/pm-assets
sudo mount -t cifs //192.168.50.104/pm-assets /mnt/pm-assets \
  -o username=drinkwish,uid=$(id -u),gid=$(id -g),iocharset=utf8
```

### macOS

Finder → 前往 → 连接服务器：

```
smb://192.168.50.104/pm-assets
```

### Linux

```bash
sudo mount -t cifs //192.168.50.104/pm-assets /mnt/pm-assets \
  -o username=drinkwish,uid=$(id -u),gid=$(id -g)
```

## 3. 目录约定

```
pm-assets/
└── iteration-hub/
    └── F-2026-11-01/
        ├── demo.mp4
        ├── screenshot-01.png
        └── ...
```

规则：

```
<project>/<feature>/<filename>
```

同一 feature 的所有大文件放一个目录，方便整体归档。

## 4. roadmap 只存 manifest

在项目目录下建 `assets.json`（会进 git，但只有文本）：

```json
{
  "root": "pm-assets/iteration-hub",
  "files": [
    {
      "feature": "F-2026-11-01",
      "name": "demo.mp4",
      "path": "F-2026-11-01/demo.mp4",
      "sha256": "…",
      "size": 12345678
    }
  ]
}
```

路径相对于 NAS 共享根目录，不写绝对路径，换机器也能用。

计算 sha256：

```powershell
Get-FileHash .\demo.mp4 -Algorithm SHA256
```

```bash
sha256sum demo.mp4
```

## 5. 容器里的访问

`deploy/.env`：

```ini
ASSETS_DIR=/vol1/1000/pm-assets
```

compose 已挂载：

```yaml
volumes:
  - "${ASSETS_DIR}:/assets"
```

容器里的路径就是 `/assets/iteration-hub/F-2026-11-01/...`。
如果容器用户 PUID 读不了，在 NAS 上：

```bash
chown -R <PUID>:<PGID> /vol1/1000/pm-assets
chmod -R u+rwX /vol1/1000/pm-assets
```

## 6. 不要把共享目录放进 git

如果共享挂载在仓库里（例如 `iteration-hub/pm-assets`），加到 `.gitignore`：

```
pm-assets/
```

更推荐挂在仓库外（`Z:`、`/mnt/pm-assets`），从根上避免误提交。

## 7. 当前限制

`pm.py` 和 dsh-pm 插件目前只同步 roadmap 里的文本/manifest，**不传输二进制**。
所以：

- 人在局域网：直接通过 SMB 写共享目录；
- DSH 会话：先挂载共享目录，再读写；
- 远程 session 无法挂载时，才需要后续加 `/api/asset` 上传/下载接口。

如果以后要让远程 session 也能传，我可以在 `pm.py` 加：

- `POST /api/asset`（token、大小限制、sha256）
- `GET /api/asset`
- 插件工具 `pm_asset_upload` / `pm_asset_list`

但在局域网内，SMB 共享是最省事、最稳的方案。
