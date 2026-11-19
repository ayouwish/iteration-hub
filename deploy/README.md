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
```

`ROADMAP` 必须指向 **my-roadmap**，不是 iteration-hub。

## 3. 首次初始化（可选）

容器首次启动时如果 roadmap 为空，会自动初始化，项目 id 取目录名（如 `my-roadmap`）。
想指定项目 id/名称，先用一次性容器初始化：

```bash
docker compose run --rm pm \
  python pm.py --roadmap /data init --name my-roadmap
```

然后把 `my-roadmap/projects/<pid>/hub.json` 改成实际项目（lanes / iterations / repos）。

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
