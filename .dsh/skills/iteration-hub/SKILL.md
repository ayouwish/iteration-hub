---
name: iteration-hub
description: Install and configure the iteration-hub pm service with a separate roadmap repository (for example my-roadmap): initialize the roadmap, deploy the pm service on a NAS for LAN access, install the dsh-pm plugin on each DSH machine, and set the server address and token. Use when the user asks to set up, deploy, or connect iteration-hub or pm.
---

# iteration-hub 安装与配置

iteration-hub 是**服务仓**（pm.py + 部署 + skill），不是数据仓。
真正的数据在独立的 roadmap 仓库里，例如 `my-roadmap`。

```
NAS                                     每台开发机
iteration-hub（服务代码）                 dsh-pm 插件
  pm.py serve  ──HTTP──▶  /api/*  ◀────  pm_projects / pm_next / pm_report
  --roadmap /data                       配置页：server + token
        ▲
        │ 挂载
  my-roadmap（roadmap 数据仓）
```

不要重写 pm.py 的状态逻辑；所有写入都通过它完成。

## 0. 先收集这些信息

| 变量 | 默认 | 说明 |
|---|---|---|
| `PM_NAS_HOST` | 必填 | NAS 的局域网 IP 或主机名，例如 `192.168.1.10` |
| `PM_PORT` | `8787` | pm 服务端口 |
| `PM_TOKEN` | 生成 | 长随机串；不要用 `123456` |
| `ROADMAP` | 必填 | **roadmap 数据仓**（my-roadmap）在 NAS 上的路径，例如 `/volume1/docker/my-roadmap` |
| `PROJECT_ID` | 必填 | 项目 id，例如 `shop-demo` |
| `PROJECT_NAME` | 同 id | 项目显示名 |
| `LANES` | `backend,web,ios,android,hos` | 按实际端删减 |
| `REPOS` | 可空 | 子项目代码仓：`name:仓库名:角色`，只登记不拉代码 |
| `PLUGIN_SOURCE` | 本地路径 | dsh-pm 插件来源：本地目录 / npm 包名 / Git 地址 |

生成 token：

```bash
openssl rand -hex 24
```

```powershell
-join ((48..57) + (97..102) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
```

## 1. 安装两个仓库

```bash
# 服务仓（固定，不要往里写业务数据）
git clone git@github.com:ayouwish/iteration-hub.git

# roadmap 数据仓（你的数据）
git clone <your-roadmap-repo>
```

用 DSH 打开 `iteration-hub` 目录作为 workspace。DSH 会自动发现
`.dsh/skills/iteration-hub/SKILL.md`；如果当前 workspace 不是它，把
`.dsh/skills/iteration-hub/` 复制到当前 workspace 的 `.dsh/skills/` 下。

> iteration-hub 自带的 `projects/my-project`、`shop-demo` 只是示例；
> 生产数据放 my-roadmap，不要把两者混在一起。

## 2. 初始化 roadmap（my-roadmap）

空目录（或只有 `.git`）可以自动初始化；非空目录会拒绝，避免误建。

```bash
python3 pm.py --roadmap ../my-roadmap init --name my-roadmap
```

如果 NAS 上没有 Python，用一次性容器（先配置好 `deploy/.env` 的 `ROADMAP`）：

```bash
cd deploy
docker compose run --rm pm python pm.py --roadmap /data init --name my-roadmap
```

初始化后检查：

```bash
python3 pm.py --roadmap ../my-roadmap projects
```

编辑 `my-roadmap/projects/<pid>/hub.json`：

- `lanes`：后端 / Web / iOS / Android / 鸿蒙，删掉不做的；
- `iterations`：当前迭代和 backlog；
- `repos`：子项目代码仓（只登记，不拉代码）；
- `people`：负责人。

## 3. 部署 pm 服务到 NAS

### 3.1 Docker（推荐）

```bash
cd deploy
cp .env.example .env
```

编辑 `.env`：

```ini
PM_TOKEN=<长随机串>
PM_PORT=8787
ROADMAP=/volume1/docker/my-roadmap
```

`ROADMAP` 必须指向 my-roadmap，不能指向 iteration-hub。
然后：

```bash
docker compose up -d --build
```

验证：

```bash
curl http://127.0.0.1:8787/healthz
# {"ok": true}
curl -H "Authorization: Bearer $PM_TOKEN" http://127.0.0.1:8787/api/meta
```

浏览器打开 `http://<NAS-IP>:8787/?token=<PM_TOKEN>`。
防火墙只放行局域网访问 8787；不要直接暴露公网。

### 3.2 systemd（NAS 有 Python 3）

```ini
# /etc/systemd/system/pm.service
[Unit]
Description=pm roadmap service
After=network.target

[Service]
WorkingDirectory=/opt/iteration-hub
Environment=PM_TOKEN=<PM_TOKEN>
ExecStart=/usr/bin/python3 pm.py --roadmap /srv/my-roadmap serve --host 0.0.0.0 --port 8787
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pm
```

### 3.3 本机临时验证

```bash
python3 pm.py --roadmap ../my-roadmap serve --host 0.0.0.0 --port 8787 --token <PM_TOKEN>
```

## 4. 在每台 DSH 机器安装 dsh-pm 插件

插件是独立包（建议仓库 `ayouwish/dsh-pm`，或 npm 包）。安装方式三选一：

- **本地目录**：`F:/code/dsh-workspace/dsh-pm`；
- **npm**：包名（如 `@ayouwish/dsh-pm`）；
- **Git**：仓库地址或 release tgz。

桌面版：**插件 → 添加插件 → 安装第三方插件 → 填来源 → 安装 → 启用**。
非桌面 profile：`dsh plugin --profile <name> add <来源>`。

安装后重启一次 DSH（加载 client 半侧），然后：

**插件 → dsh-pm → 配置**，填：

- 服务器地址：`http://<NAS-IP>:8787`
- Token：`<PM_TOKEN>`
- 点 **测试连接**，应显示项目数；
- 点 **保存**。

配置写入 `~/.pm/client.json`，工具每次调用都会重新读取，**保存后不需要再重启**。
也可以用 CLI：

```bash
pm connect http://<NAS-IP>:8787 --token <PM_TOKEN>
```

## 5. 验证

在新会话里：

```text
pm_projects          # 应显示 my-roadmap 上的项目
pm_bind <PROJECT_ID> # 本会话绑定项目
pm_queue             # 派工队列
pm_next              # 领任务
pm_report --status done --rev r1
```

如果 `pm_projects` 报 `fetch failed` / 401：

- 地址是否带 `http://`、端口是否 8787；
- token 是否一致（`pm serve --token` / `PM_TOKEN`）；
- NAS 防火墙是否放行局域网；
- 用 `curl` 在客户端机器上直接测 `/api/meta`。

## 6. 日常使用

- 人类：看板 `http://<NAS-IP>:8787/?token=...`，派工/看全局；
- 会话：`pm_projects` / `pm_next` / `pm_task` / `pm_report` / `pm_sync`；
- 代码改动在**子项目代码仓**里 commit/push，pm 只记录进度；
- roadmap 数据仓（my-roadmap）用 `pm_sync` 或 git 手动提交。

## 7. 安全

- token 用长随机串，不要提交到仓库；
- 只监听局域网；公网访问加 nginx HTTPS + 认证；
- `PM_TOKEN` 放在 NAS 的 `.env` 或 systemd Environment，不要写进 README；
- 插件配置页保存的 token 在 `~/.pm/client.json`，属于本机凭据，不要提交。
