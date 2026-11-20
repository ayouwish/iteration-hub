# NAS 部署 pm 服务（iteration-hub + my-roadmap）

- **iteration-hub**：服务仓，提供 `pm.py`、Dockerfile、compose；
- **my-roadmap**：roadmap 数据仓，挂载给服务使用。

```
NAS
iteration-hub/  ── docker compose ──▶ pm 容器
  pm.py                                 --roadmap /data
                                           ▲
                                           │ 挂载
my-roadmap/  ─────────────────────────────────┘
```

## 前置

- NAS 支持 Docker / Container Manager（Synology、QNAP、TrueNAS 等）；
- 局域网内能访问 NAS 的 8787 端口；
- `my-roadmap` 已创建（空目录也可以，首次启动会自动初始化）。

## 1. 把两个仓库放到 NAS

```bash
git clone git@github.com:ayouwish/iteration-hub.git /volume1/docker/iteration-hub
git clone <your-roadmap-repo> /volume1/docker/my-roadmap
cd /volume1/docker/iteration-hub/deploy
```

没有 git 时，直接把两个目录复制到 NAS 也可以。

## 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
PM_TOKEN=<长随机串，例如 openssl rand -hex 24>
PM_PORT=8787
ROADMAP=/volume1/docker/my-roadmap
PM_SSH_DIR=/volume1/docker/pm-ssh
PUID=1000
PGID=1000
GIT_NAME=your-name
GIT_EMAIL=you@example.com
```

`ROADMAP` 必须指向 **my-roadmap**，不是 iteration-hub。

`PM_SSH_DIR` 是 NAS 上存放 **pm-wish 的 deploy key** 的目录，会只读挂载到容器的
`/ssh`。里面至少要有：

```
id_ed25519       # 私钥，chmod 600
known_hosts      # ssh-keyscan github.com > known_hosts
```

生成并添加 deploy key：

```bash
mkdir -p /volume1/docker/pm-ssh && chmod 700 /volume1/docker/pm-ssh
ssh-keygen -t ed25519 -f /volume1/docker/pm-ssh/id_ed25519 -N ''
ssh-keyscan github.com > /volume1/docker/pm-ssh/known_hosts
chmod 600 /volume1/docker/pm-ssh/id_ed25519
```

把 `id_ed25519.pub` 加到 GitHub → pm-wish 仓库 → **Settings → Deploy keys**，
勾选 **Allow write access**。

容器用 `PUID`/`PGID` 运行（`id -u` / `id -g` 得到），这样 `/data` 里的文件归 NAS 用户所有，
不会出现 root 所有或 Git “dubious ownership”。`PM_SSH_DIR` 目录权限建议 700，私钥 600，
属主是 `PUID` 对应用户。

验证：

```bash
docker compose exec pm ssh -T git@github.com
docker compose exec pm git -C /data ls-remote origin
```

## 3. 初始化 roadmap 并添加项目

`project add` 会在 roadmap 为空时自动建 hub，已存在时只加项目：

```bash
docker compose run --rm pm python pm.py --roadmap /data project add \
  --id <PROJECT_ID> --name "<PROJECT_NAME>" \
  --lanes backend,web,ios,android,hos \
  --default
```

已有项目备份时，把备份放到 roadmap 卷的 `import/` 下再导入：

```bash
docker compose run --rm pm python pm.py --roadmap /data project add \
  --id <PROJECT_ID> --name "<PROJECT_NAME>" \
  --from /data/import/<project> --default
```

检查：

```bash
docker compose run --rm pm python pm.py --roadmap /data projects
```

## 4. 启动

```bash
docker compose up -d --build
```

验证：

```bash
curl http://127.0.0.1:8787/healthz
# {"ok": true}

curl -H "Authorization: Bearer $PM_TOKEN" http://127.0.0.1:8787/api/meta
```

浏览器打开：

```
http://<NAS-IP>:8787/?token=<PM_TOKEN>
```

## 5. 客户端（每台 DSH 机器）

安装 `dsh-pm` 插件后，在 **插件 → dsh-pm → 配置** 填：

- 服务器地址：`http://<NAS-IP>:8787`
- Token：`<PM_TOKEN>`
- 测试连接 → 保存

或 CLI：

```bash
pm connect http://<NAS-IP>:8787 --token <PM_TOKEN>
```

## 6. 不用 Docker 时（systemd）

NAS 有 Python 3 时，用 [pm.service](pm.service)：

```bash
sudo cp pm.service /etc/systemd/system/pm.service
# 编辑 WorkingDirectory / ExecStart（--roadmap /srv/my-roadmap）/ PM_TOKEN
sudo systemctl daemon-reload
sudo systemctl enable --now pm
```

## 7. 安全

- 只放行局域网访问 8787，不要直接暴露公网；
- 需要公网时，用 nginx/Caddy 加 HTTPS 和认证，再转发到 8787；
- `PM_TOKEN` 只放在 `.env` / systemd Environment，不要提交；
- 定期备份 `ROADMAP` 指向的 my-roadmap（它就是 roadmap git 仓库）。
