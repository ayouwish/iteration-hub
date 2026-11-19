# 通用化：任何项目，3 分钟拉起一套协同开发系统

这套东西不是给 my-project 定制的。`pm.py` 里没有任何平台名、仓库名或机器名是写死的——
**hub（仓库）、platform（端）、machine（机器）都是配置**，所以同一个工具可以在任意项目上复用。

> 迭代一致性与 AI 时代的多端一致性方案见 [CONSISTENCY.md](CONSISTENCY.md)；
> 系统本身的设计取舍见 [DESIGN.md](DESIGN.md)。

---

## 1. 新项目：三条命令

```bash
# 1) 生成 roadmap 仓库（自动带 pm.py、hub.json、tokens、目录结构、git init）
python pm.py scaffold --dir ../my-roadmap --name my-app --preset multi \
    --people "me:我,ai-1:AI Agent 1" \
    --repos "api:my-api:后端+Web,app:my-app:iOS/Android/鸿蒙"

# 2) 推到私有仓库（GitHub）
cd ../my-roadmap
git add -A && git commit -m "init roadmap"
git remote add origin git@github.com:<you>/my-roadmap.git
git push -u origin main

# 3) 每台开发机（Windows / Mac / Linux 各一次）
git clone git@github.com:<you>/my-roadmap.git && cd my-roadmap
python pm.py setup --machine windows-home    # 写 .machine + 装 post-merge hook
# DSH 会话里再绑定一次项目：pm bind <pid>
```

生成的东西（一个 pm 仓库，多个项目）：

```
my-roadmap/
├── pm.py                 # 零依赖工具（scaffold 自动复制一份，可离线用）
├── hub.json              # 仓库级：projects / machines / people / default_project
├── projects/
│   └── <pid>/
│       ├── hub.json      # 项目级：lanes / iterations / repos / 阈值
│       ├── features/     # 需求定义（每个需求一个 JSON，含方案与 revision）
│       ├── events/       # 只追加事件日志（每台机器每月一个文件）
│       ├── design/tokens.json
│       ├── spec/screens/
│       └── shots/        # 跨端截图基线
├── out/                  # 生成物：index.html（项目总览）+ <pid>/dashboard.html
└── README.md
```

`--preset` 可选 `multi`（默认，后端+Web+iOS+Android+鸿蒙）、`mobile`、`web`、`api`；
`pm.py scaffold --list-presets` 查看。

---

## 2. 参数语法

| 参数 | 语法 | 示例 |
|---|---|---|
| `--platforms` / `--lanes` | `id:显示名`，逗号分隔 | `backend:后端,admin:管理后台,web:Web,ios:iOS` |
| `--people` | `id:名字`，逗号分隔（人 / AI Agent 都行） | `me:我,ai-1:AI Agent 1` |
| `--machines` | `id:显示名[:ro]`，`ro` = 只读机器 | `windows-home:家用 Windows,mac-weekend:Mac,cloud:云服务器:ro` |
| `--repos` | `id:仓库名:职责` | `api:my-api:后端+Web,app:my-app:iOS/Android` |
| `--iteration` | `id:开始:结束:目标` | `2026-11:2026-11-02:2026-11-29:iOS 首发` |

平台数量不限。比如一个"后端 + 管理后台 + 小程序 + App"的项目：

```bash
python pm.py scaffold --dir ../shop-roadmap --name shop --no-git \
  --platforms "backend:后端,admin:管理后台,miniapp:小程序,app:App" \
  --machines "windows-home:家用 Windows,mac-weekend:Mac,cloud:云服务器:ro"
```

后续要加平台：改 `hub.json` 的 `platforms` / `platform_labels`，已有需求里新平台默认按 `na` 处理。

---

## 3. 一个 pm 仓库，多个项目

`hub.json` 里配置 `projects`，每个项目在 `projects/<pid>/` 下独立（配置/需求/事件/规格/令牌/截图）：

```json
{
  "name": "ayouwish-workspace",
  "machines": { "windows-home": {...}, "mac-weekend": {...}, "cloud": {...} },
  "people": { "me": {"name": "我"}, "ai-1": {"name": "AI Agent 1"} },
  "projects": {
    "shop-demo": { "name": "商城 Demo", "path": "projects/shop-demo" },
    "shop-demo":     { "name": "商城 Demo", "path": "projects/shop-demo" }
  },
  "default_project": "shop-demo"
}
```

每个 `projects/<pid>/hub.json` 放自己的 `lanes` / `iterations` / `repos` / 阈值。
一个 pm 实例管所有项目；面板 `/` 是项目总览，`/p/<pid>` 是项目看板。

### 会话绑定项目（一次）

```bash
pm projects                 # 看有哪些项目、当前会话绑了哪个
pm bind shop-demo           # 把当前 DSH 会话绑定到 shop-demo
pm next                     # 之后不用再指定项目
pm bind                     # 查看当前绑定
pm bind --clear             # 解除绑定
```

也可以临时指定：`pm next --project shop-demo`、`pm task shop-demo/S-2:web`。
没有绑定项目时，`pm task <需求ID>` 会按需求 ID 自动定位项目（需求 ID 全局唯一）。

### 加一个项目

```bash
# 复制一个项目目录，改 id/配置，并在顶层 hub.json 的 projects 里登记
cp -r projects/shop-demo projects/new-project
# 编辑 projects/new-project/hub.json（name/lanes/iterations/repos）
# 编辑 hub.json 的 projects 加一行 "new-project": {"name": "...", "path": "projects/new-project"}
python pm.py projects       # 确认出现
```

### 指向另一个 pm 仓库

```bash
python ~/tools/pm.py --hub ~/roadmaps/other build
```

云服务器聚合（一个 pm 仓库即可）：

```bash
*/5 * * * * cd /srv/pm && git pull -q --rebase && python3 pm.py build && rsync -a out/ /srv/www/pm/
```

```nginx
location /pm/ { alias /srv/www/pm/; auth_basic "pm"; auth_basic_user_file /etc/nginx/pm.htpasswd; }
```

> 建议每个 pm 仓库自带一份 `pm.py`（离线、版本随仓库走）；也可以用本机一份全局的，`--hub` 指定仓库。

---

## 3.5 地址与远程部署

本地面板默认 `http://127.0.0.1:8787`。把 pm 放到服务器给多台机器/会话用：

**服务器**

```bash
PM_TOKEN=<随机串> python3 pm.py serve --host 0.0.0.0 --port 8787
```

systemd：

```ini
[Unit]
Description=pm hub
[Service]
WorkingDirectory=/srv/pm
Environment=PM_TOKEN=<随机串>
ExecStart=/usr/bin/python3 pm.py serve --host 0.0.0.0 --port 8787
Restart=always
[Install]
WantedBy=multi-user.target
```

nginx（建议独立子域名，`/p/<pid>` 这类绝对路径才不会错）：

```nginx
server {
  server_name pm.example.com;
  location / {
    proxy_pass http://127.0.0.1:8787;
    auth_basic "pm"; auth_basic_user_file /etc/nginx/pm.htpasswd;
  }
}
```

**客户端（每台机器一次）**

```bash
pm connect https://pm.example.com --token <token>   # 保存到 ~/.pm/client.json
pm projects                                          # 验证连上了
```

或临时用环境变量：

```bash
PM_URL=https://pm.example.com PM_TOKEN=<token> pm next
```

之后 `next / task / report / dispatch / queue / projects / bind` 自动走远程 API；
加 `--local` 强制走本地仓库。地址优先级：
`--server` > `PM_URL` > `~/.pm/client.json`（`pm connect`）> 本地模式。

token 优先级：`PM_TOKEN` > `~/.pm/client.json` 里的 token；`serve --token` 用于服务端。
**远程写必须设 token**；面板再加 nginx basic auth + HTTPS。只读展示可用 `--read-only` 且不带 token。

---

## 4. 每台机器的角色（和项目无关）

| 机器 | 角色 | 动作 |
|---|---|---|
| 家用 Windows（日常） | **写入端** | `pm set` / `pm change` / `pm sync` |
| Mac（周末） | **写入端** | 同上，iOS 相关 |
| 云服务器 | **只读聚合** | cron: `git pull && pm build`，发布 `out/` |
| GitHub | 远端真相 | 备份 / PR / 换机恢复 |

只有写入端产生事件，云服务器永远不写——所以不需要锁、不需要服务端、不会冲突。

---

## 5. 日常工作流：人类看板，DSH 会话干活

一次性（每台机器）：

```bash
python pm.py setup --machine windows-home   # .machine + 启动器 + post-merge hook
```

### 人类

1. 双击 **`start-pm.cmd`**（Windows）/ **`start-pm.command`**（Mac）/ 运行 **`./start-pm.sh`**（Linux）；
2. 在任务弹窗点 **「派给 DSH 会话」** → 任务进入派工队列（提示词自动复制）；
3. 看板看全局、点「同步」推给其他机器（可勾选「保存后自动同步」）。

### DSH 会话（执行主体）

```bash
pm bind shop-demo                # 每个会话绑定一次项目
pm next                          # 领任务：队列优先，其次最高优先级可开工
pm task F-2026-11-02:ios         # 领指定任务
pm report --status review --note "自测通过"
pm report --status done --rev r2 --note "完成"
pm new / pm change / pm plan     # 提交 / 修改需求
pm check && pm sync              # 收尾
```

领取任务时自动把状态改为进行中，并记录 `DSH_SESSION_ID`；之后 `pm report` 不带任务 ID 也能自动定位本会话的任务。
完整协议见 [DSH.md](DSH.md)。

### Linux

- **桌面 Linux**：`./start-pm.sh`（setup 已 chmod +x）；
- **无 GUI 的服务器**：`pm serve --read-only` 给 nginx 发布只读面板；
  需要写面板时从本机 `ssh -L 8787:127.0.0.1:8787 server`，再在本地浏览器打开 `http://127.0.0.1:8787/`；
- 面板写操作只追加本机事件文件，断网可用；云端面板仍然只读。

### 批量 / 脚本

```bash
python pm.py new --id F-1 --title "家庭邀请码" --priority P0
python pm.py plan F-1 --doc docs/family-invite.md --approve
python pm.py change F-1 --rev r2 --breaking --affects web,ios,android,hos
python pm.py shots --check
python pm.py check && python pm.py sync
```

---

## 5.5 任务看板（方案先行 · 多泳道 · 多人）

面板新增一块看板：5 列（待办 / 进行中 / 待验收 / 阻塞 / 完成），
卡片 = 方案 × 泳道，显示负责人、revision、ETA、依赖状态；顶部可按泳道 / 负责人筛选。

- **方案没批准就开工** → 红色；
- **依赖没完成就开工** → 黄色；
- **滞后于最新 revision** → 按变更级别红/黄；
- 每天站会看同一块板：谁在做什么、谁被卡住、什么可开工。

多人写入仍然无冲突：每台机器只写自己的事件文件，`assign` / `status` / `dep` 都是追加事件，
折叠时按 `(ts, machine, id)` 取后者，历史可查。人、AI Agent 都可以是负责人。

---

## 6. 命令总表

| 命令 | 作用 |
|---|---|
| `pm scaffold --dir …` | 生成新 pm 仓库（含一个项目目录） |
| `pm init` | 在当前目录初始化 pm 仓库 + 项目 |
| `pm projects` / `pm bind` | 列出项目 / 当前会话绑定项目 |
| `pm connect` | 配置远程 pm 地址与 token（`~/.pm/client.json`） |
| `pm --server <url>` / `PM_URL` | 临时走远程 API（`--local` 强制本地） |
| `pm setup --machine …` | 本机初始化：`.machine` + hook + 启动器（Windows/Mac/Linux） |
| `pm new` / `pm set` / `pm change` | 需求 / 状态 / 变更（revision） |
| `pm plan` / `pm assign` / `pm dep` | 方案闸门 / 指派负责人 / 任务依赖 |
| `pm dispatch` / `pm queue` | 派工进队列 / 查看派工队列 |
| `pm next` / `pm task` / `pm report` | 会话领任务 / 领指定任务 / 自动上报（自动带 session） |
| `pm contract` | 冻结/解冻接口契约 |
| `pm log` | 给需求加备注 |
| `pm list` | 终端需求矩阵（含 revision 与漂移） |
| `pm build` | 折叠事件 → 面板（含任务看板）+ 快照 + 生成的文档 |
| `pm gen` | 生成各端令牌 + 屏幕规格 md（build 自动调用） |
| `pm shots` | 生成跨端截图对齐页；`--check` 检查缺失 |
| `pm check` | 静态检查：方案/依赖/负责人/revision/令牌/屏幕规格 |
| `pm sync` | pull → build → commit → push |
| `pm serve` | 本地可写面板（`--open` 自动开浏览器；`--read-only` 只读；非本机地址必须只读） |
| `pm doctor` | 自检 |
| `pm --hub <dir> …` | 对任意 hub 仓库执行任意命令 |

---

## 7. 从已有项目迁移

1. 先只录**当前迭代在做的 10–15 条**，不要一次把 backlog 全录进来；
2. 现有的 `feature-index.md` / 需求文档保持不动，`features/*.json` 里用 `spec` 字段指过去；
3. 两个代码仓库完全不动；可选加 commit trailer `Refs: F-1`；
4. 第一次 `pm sync` 之后，面板就是新的进度真相源，手写进度表可以退休。

---

## 8. 常见问题

| 问题 | 处理 |
|---|---|
| 两台机器同时改了同一个需求 | 两个事件都保留，折叠按 `(ts, machine, id)` 取后者；历史仍可查 |
| 忘了 pull 就改了 | 不会冲突（每台机器只写自己的事件文件）；下次 `pm sync` 自动 rebase |
| 断网 | 照常 `pm set`，`pm sync` 会跳过 push，联网再同步 |
| 机器时间不准 | 只影响显示顺序，不丢数据；云服务器时间可作基准 |
| Windows 上 `python3` 不存在 | 用 `python pm.py`；hook 脚本里两种都试 |
| 面板要不要暴露公网 | 只读页面 + nginx basic auth；或只在内网 |
| 换电脑 | `git clone` + `pm setup --machine <名字>` 即可 |
| 不想每次双击启动器 | Windows：把 `start-pm.cmd` 固定到任务栏/开始菜单；Mac：把 `start-pm.command` 拖进登录项 |
| 启动器窗口能关吗 | 关掉窗口 = 停止本地面板；数据已经写进事件文件，不会丢 |

---

## 9. 边界

- 不做实时协作：一个人 + 5 分钟延迟足够；出现第二个人时再考虑小服务；
- 不做移动端写入：手机上只看面板；
- 云服务器只读：所有写入都发生在开发机；
- 数据量：一年 1500 条事件、300 KB 量级，文件 + git 足够；
- 升级工具：`scaffold` 会把当前 `pm.py` 复制进新仓库；旧仓库要升级就手动替换 `pm.py`（单文件，无依赖）。
