# iteration-hub

多端迭代管理器的**服务仓**：`pm.py` + NAS 部署 + DSH skill + 示例数据。
你的**数据**在独立的 roadmap 仓（例如 `my-roadmap`）；DSH 插件 `dsh-pm` 是另一个独立包。

## 三个东西，角色固定

| 仓库 / 包 | 角色 | 用户要做什么 |
|---|---|---|
| **iteration-hub**（本仓库） | 服务端工具 `pm.py`、NAS 部署、DSH skill、示例数据 | clone 下来，**不要改数据** |
| **my-roadmap**（你的 roadmap 仓） | 真正的数据：`projects/`、`events/`、`spec/`、`design/`、`shots/` | 用 git 管理，NAS 挂载它 |
| **dsh-pm**（插件包） | DSH 客户端：agent 工具 + 配置页 | 每台机器安装一次 |

```
NAS                                     每台开发机
iteration-hub（服务代码）                 dsh-pm 插件
  pm.py serve  ──HTTP──▶  /api/*  ◀────  pm_projects / pm_next / pm_report
  --roadmap /data                       配置页：server + token
        ▲
        │ 挂载
  my-roadmap（roadmap 数据仓）
```

`iteration-hub/projects/shop-demo` 只是示例；生产数据放 `my-roadmap`。

## 用户安装流程

```bash
# 1) 服务仓（固定）
git clone git@github.com:ayouwish/iteration-hub.git

# 2) 你的 roadmap 数据仓
git clone <your-roadmap-repo>
```

1. 用 DSH 打开 `iteration-hub` 目录作为 workspace（`.dsh/skills/iteration-hub/SKILL.md` 会自动被发现）；
2. 对 agent 说：

   > 读取 `.dsh/skills/iteration-hub/SKILL.md`，按里面的步骤安装。

3. agent 会确认：NAS 地址、端口、token、`ROADMAP` 路径（指向 `my-roadmap`）、项目 id/泳道/代码仓；
   然后完成：初始化 my-roadmap → NAS 部署 → 安装 dsh-pm 插件 → 配置地址 → 验证。

如果当前 workspace 不是 `iteration-hub`，把 `.dsh/skills/iteration-hub/` 复制到当前 workspace 的 `.dsh/skills/` 下。

> 视频/大图等多媒体资源走 NAS 共享，详见 [ASSETS.md](ASSETS.md)。

## NAS 部署

`deploy/.env` 必须指定 roadmap 仓：

```ini
PM_TOKEN=<长随机串>
PM_PORT=8787
ROADMAP=/volume1/docker/my-roadmap
```

```bash
cd deploy
cp .env.example .env
docker compose up -d --build
```

验证：`http://<NAS-IP>:8787/?token=<PM_TOKEN>`。
详见 [deploy/README.md](deploy/README.md)。

## 插件发布到哪里

`dsh-pm` 是独立于本仓库的 DSH 插件包：

1. **GitHub `ayouwish/dsh-pm` + tag**：插件管理器支持 Git / tgz / 本地目录，个人和局域网足够；
2. **npm `@ayouwish/dsh-pm`（推荐）**：`npm publish --access public`，之后所有机器填包名即可安装；
3. 开发阶段直接填本地路径。

本仓库（iteration-hub）**不发 npm**：它是服务仓 + skill + 部署文件，GitHub clone 即可。

## 手工 CLI

```bash
python3 pm.py --roadmap /path/to/my-roadmap project add \
  --id <project> --name "<name>" --lanes backend,web,ios,android,hos --default
python3 pm.py --roadmap /path/to/my-roadmap projects
python3 pm.py --roadmap /path/to/my-roadmap serve --host 0.0.0.0 --port 8787 --token <token>
```

空目录（或只有 `.git`）时，`serve` / `build` 会自动初始化；非空目录会拒绝，避免误建。

## 目录

```
pm.py                      # 零依赖 CLI / 服务（固定）
.dsh/skills/iteration-hub/ # 给 agent 的安装 skill
deploy/                    # NAS：Dockerfile / compose / systemd / 说明
projects/                  # 示例项目（生产数据不在这里）
events/                    # 示例事件（生产数据不在这里）
```

## 安全

- token 用长随机串，只放 NAS 的 `.env` / systemd Environment，不要提交；
- 只监听局域网；公网访问加 HTTPS + 反向代理认证；
- 客户端 token 存在 `~/.pm/client.json`，不要提交。
