> 推荐按 `.dsh/skills/iteration-hub/SKILL.md` 由 agent 自动安装；本文件是手工参考。

# 5 分钟上手（三仓模型）

```
pm 工具仓（clone 一次）        roadmap 数据仓（scaffold 生成）      子项目代码仓（已有）
~/pm/pm.py          ──绑定──▶  ~/my-roadmap/                  ◀──引用──  your-app …
                               hub.json + projects/<pid>/                   （写代码，pm 不碰）
```

- **pm 工具仓**：只有工具（`pm.py`），独立 repo；
- **roadmap 数据仓**：需求/事件/规格/面板数据，独立 repo，push 到 GitHub；
- **子项目代码仓**：你真正写代码的仓库（后端/Web/移动端），在 `projects/<pid>/hub.json` 的 `repos` 里登记。

---

## 1. 下载 pm（一次性）

```bash
git clone <pm 工具仓> ~/pm
alias pm='python ~/pm/pm.py'          # Windows: doskey pm=python C:\pm\pm.py $*
```

## 2. 建 roadmap 数据仓（一次性）

```bash
python ~/pm/pm.py scaffold --dir ~/my-roadmap --name my-workspace
cd ~/my-roadmap
git add -A && git commit -m "init roadmap"
git remote add origin git@github.com:<you>/my-roadmap.git
git push -u origin main
```

生成 `hub.json`（项目列表）+ `projects/<pid>/`（每个项目自己的配置/需求/事件/规格/令牌/截图）。
`scaffold` 默认**不**复制 pm.py；想离线自带一份加 `--vendor`。

> 目录是空的（或只有 `.git`）时，也可以跳过 scaffold，直接
> `pm.py --roadmap <dir> serve`：启动时发现没有 `hub.json` 会自动初始化；
> 非空目录会拒绝自动初始化，避免在代码仓里误建。

## 3. 每台机器（一次性）

```bash
python ~/pm/pm.py roadmap ~/my-roadmap      # 记住默认 roadmap（也可用 --roadmap 临时指定）
python ~/pm/pm.py setup --machine windows-home
```

- Windows 双击 `start-pm.cmd`，Mac 双击 `start-pm.command`，Linux 运行 `./start-pm.sh`；
- 启动器已经写死了工具路径 + `--roadmap`，不需要把 pm.py 放进 roadmap；
- 浏览器打开 `http://127.0.0.1:8787/`：项目总览 → 点项目进看板。

## 4. 登记子项目代码仓

编辑 `projects/<pid>/hub.json` 的 `repos`（只是引用，不拉代码）：

```json
{
  "name": "shop-demo",
  "lanes": ["backend", "web", "ios", "android", "hos"],
  "repos": {
    "server":  { "name": "shop-demo",     "role": "后端 + Web" },
    "clients": { "name": "my-app",           "role": "iOS / Android / 鸿蒙" }
  }
}
```

DSH 会话领任务时，任务简报里会带上这些仓库名。

## 5. 人类：只做三件事

| 动作 | 怎么做 |
|---|---|
| 派工 | 看板点任务卡片 →「派给 DSH 会话」→ 进队列 |
| 看全局 | 项目总览：可发布 / 阻塞 / 待认领 / 漂移 / 风险 |
| 同步 | 面板顶部「同步」（可勾选保存后自动同步） |

## 6. DSH 会话：绑定项目后用命令干活

```bash
pm bind shop-demo                # 每个会话绑定一次
pm next                          # 领任务：队列优先，其次最高优先级可开工
pm task F-2026-11-02:ios         # 领指定任务
pm report --status review --note "自测通过"
pm report --status done --rev r2 --note "完成"
pm check && pm sync              # 只提交 roadmap 数据，不碰代码仓
```

会话的代码改动在**子项目代码仓**里自己 commit/push；pm 只记录进度和需求。

## 7. 常用速查

| 我想… | 命令 |
|---|---|
| 新建需求 | `pm new --id F-1 --title "..." --priority P1` |
| 改某端状态 | `pm set F-1 web done --rev r1` |
| 需求变更 | `pm change F-1 --rev r2 --breaking --note "..."` |
| 方案批准 | `pm plan F-1 --doc docs/x.md --approve` |
| 派工 / 看队列 | `pm dispatch F-1:ios` / `pm queue` |
| 终端矩阵 | `pm list` |
| 生成/自检 | `pm build` / `pm check` / `pm doctor` |
| 切换 roadmap | `pm --roadmap <dir> ...`（临时）或 `pm roadmap <dir>`（默认） |

## 8. 远程部署（可选）

```bash
# 服务器（工具 + roadmap 都在服务器上）
PM_TOKEN=<随机串> python3 ~/pm/pm.py --roadmap /srv/my-roadmap serve --host 0.0.0.0 --port 8787

# 客户端（每台机器一次）
pm connect https://pm.example.com --token <随机串>
pm projects
```

之后 `next/task/report/dispatch/queue/projects/bind` 自动走远程；`--local` 强制本地。
systemd / nginx / HTTPS 见 [GUIDE.md](GUIDE.md) §3.5。

## 9. 出问题

```bash
pm roadmap            # 当前默认 roadmap
pm doctor             # 仓库/项目/事件/git
pm check              # 方案/依赖/负责人/revision/规格
pm --roadmap <dir> ...  # 临时换 roadmap
```
