# 多端迭代管理 + 开发面板 · 设计说明

> 场景：一个人同时开发 1 个后端 + 4 个前端（Web / iOS / Android / 鸿蒙），
> 后端最快、Web 与后端同仓、移动端各自仓库，开发机分散（Windows 日常 / Mac 仅周末 / 云服务器 / GitHub）。
> 目标：管理总需求池与五端进度，**尽量不引入第三方依赖**。
>
> 通用化（任意项目 3 分钟拉起）见 [GUIDE.md](GUIDE.md)；
> 迭代一致性与 AI 时代的多端一致性见 [CONSISTENCY.md](CONSISTENCY.md)。

---

## 0. 结论（先看这段）

整套系统只有三样东西，没有数据库、没有后端服务、没有 npm/pip 包：

1. **一个私有 git 仓库** `english-roadmap` 作为「需求与进度的唯一真相源」——文件即数据；
2. **一个零依赖 CLI** `pm.py`（Python 标准库）——改状态、折叠状态、生成面板；
3. **一个静态 HTML 面板** + 云服务器上一条 cron——只读聚合与发布。

关键机制只有三条：

| 机制 | 作用 |
|---|---|
| **每台机器每月一个只追加事件文件**（`events/<machine>/<YYYY-MM>.jsonl`） | 多机同步在结构上就不可能冲突，不需要锁、不需要合并策略 |
| **契约冻结是唯一同步点**（`pm contract` + 面板红色告警） | 后端快不是问题；接口不受控才是问题 |
| **面板 / 文档全部由状态生成，不手写** | 杜绝「文档写完了、代码没跟上」两张皮 |
| **本地可写面板** | 双击启动器打开 localhost 页面，点格子改状态 / 指派 / 记变更；云端仍只读 |

云服务器只做**只读聚合**（pull → build → 静态发布），永远不写状态——这是让云参与进来又不破坏模型的关键。

---

## 1. 现状与约束

### 机器

| 机器 | 可用性 | 承担 | 在系统里的角色 |
|---|---|---|---|
| 家用 Windows | 日常 | 后端 / Web / Android / 鸿蒙构建 | **写入端**（主要） |
| Mac 笔记本 | 仅周末 | iOS 开发与构建 | **写入端**（次要） |
| 云服务器 | 常驻 | 已有部署（openresty / Docker） | **只读聚合端**：pull + build + 发布面板 |
| GitHub | 随时 | 代码托管 | **远端真相 / 备份 / PR** |

### 仓库

| 仓库 | 内容 |
|---|---|
| `<your-org>/my-project` | 后端 + Web(Next.js) + voice + deploy + docs（api-contract 等） |
| `<your-org>/my-app` | iOS / Android / 鸿蒙 + 设计文档（feature-index、screens-matrix…） |
| `ayouwish/english-roadmap`（新增） | 本系统：需求池 + 事件日志 + 配置，几十 KB |

### 已有文档的隐患

现在 `my-app/docs/feature-index.md` 是**手写**的进度表（✅/🔄/📋），
`backend-contract.md` 记录契约版本。问题不在于写得不好，而在于：

- 手写进度 → 永远滞后于真实进度，且每台机器都要改同一个文件 → 必然冲突；
- 状态散落在两个仓库的 Markdown 里 → 没有一个地方能回答「这个迭代五端到哪了」；
- 契约版本在文档里、代码在另一个仓库 → 前端不知道能不能开工。

所以本设计的第一原则：**状态只存一份，且只以追加方式变化；展示层全部生成。**

---

## 2. 目标与非目标

**目标**

- 一屏看清：总需求池、当前迭代、五端进度、阻塞、契约状态、下一步；
- 三台机器交错提交**永不冲突**，断网也能干活；
- 手机能看（周末在外也能确认状态）；
- 维护成本 ≈ 0：不需要常驻服务、不需要数据库、不需要账号系统；
- 新增第三方依赖 = 0。

**非目标**

- 不做 CI/CD、不做自动部署（现有 deploy 流程不动）；
- 不做代码评审 / issue 讨论（GitHub PR 已够）；
- 不做实时协作（一个人，5 分钟延迟完全可接受）；
- 不做权限系统（面板只读 + nginx basic auth）。

---

## 3. 总体结构

```
                  GitHub: ayouwish/english-roadmap（私有，真相源）
                     ▲  pull/push            ▲ pull/push
        ┌────────────┘                       └──────────────┐
        │                                                   │
  Windows 家用（日常）                                 Mac（周末）
  后端/Web/Android/鸿蒙                               iOS
  pm set / pm sync                                    pm set / pm sync
        │                                                   │
        └──────────────┬────────────────────────────────────┘
                       │ git push
                       ▼
                云服务器（只读聚合）
                cron: git pull && python3 pm.py build
                      │
                      ▼
        openresty/nginx 静态目录 → https://<域名>/hub/
        （手机、任何浏览器都能看；面板本身无写接口）
```

代码仓库（`my-project`、`my-app`）**完全不参与状态管理**，最多加一行 commit trailer：

```
feat(ios): 家庭邀请码卡片

Refs: F-2026-11-09
```

这条 trailer 让「需求 ↔ 提交」可追溯，也是后续 `pm commits` 的钩子（阶段 2）。

---

## 4. 为什么用 git 当数据库

| 维度 | 说明 |
|---|---|
| 成本 | 已有 git、已有 GitHub、已有三台机器，零新增 |
| 历史 | 每次状态变化都是一条事件，天然审计日志，可回溯任意一天 |
| 备份 | push 到 GitHub 即备份，不需要额外备份策略 |
| 离线 | 状态写入发生在本地工作副本，断网照样记，联网再 push |
| 体量 | 示例 16 个需求 + 67 条事件 ≈ 30 KB；按每周约 30 条事件估算，一年约 1500 条、300 KB 量级；折叠 + 生成面板整条命令约 0.25 s（含 Python 启动） |
| 代价 | 没有实时协作；面板有 0–5 分钟延迟；需要养成 `pm sync` 习惯 |

> 什么时候该换 SQLite + 小服务？当出现**第二个人的写入**、或需要**移动端直接改状态**、或需要**实时**时。
> 现在都不成立，所以不值得为它维护一个常驻服务和数据库。

---

## 5. 无冲突同步协议（核心设计）

### 五条规则

1. **状态变化只追加事件，绝不修改共享文件。**
   一次 `pm set` 只是在 `events/<machine>/<YYYY-MM>.jsonl` 末尾写一行。

2. **每台机器每月一个文件。**
   Windows 写 `events/windows-home/2026-11.jsonl`，Mac 写 `events/mac-weekend/2026-11.jsonl`。
   两台机器**永远不会写同一个文件** → git 层面不存在文本冲突。

3. **事件是「设值」而不是「增量」，折叠按全序进行。**
   事件形如 `{"type":"status","feature":"F-…","platform":"ios","value":"doing"}`。
   折叠时按 `(ts, machine, id)` 排序后依次覆盖 → 同样的输入永远得到同样的输出，
   与事件到达顺序、pull 顺序无关。

4. **需求定义每个文件只有一个创建者。**
   `features/F-….json` 创建后基本不改；要改标题/验收点用 `type:"feature"` 事件，
   避免两台机器改同一个 JSON。

5. **生成物不入库。**
   `out/`（面板、快照、生成的 Markdown）被 `.gitignore` 忽略，由云端或本地 `pm build` 生成。
   派生文件不参与版本控制 → 又消除一类冲突。

### 两台机器同时改同一个需求同一个平台会怎样？

两个事件都保留（不同文件、不同 id），折叠时按时间取后者生效，**前一条仍留在历史里**。
面板会显示当前值，`grep events/` 能看到「谁在什么时候改的」。个人开发场景下这已经足够；
如果真需要强一致，才值得上服务端。

### 时钟

全部使用 UTC ISO 时间戳，云服务器时间作为基准。
极端时钟漂移只影响「显示顺序」，不会丢数据；折叠有确定性的 tie-break（machine、id）。

### 日常命令（每台机器一样）

```bash
git pull --rebase --autostash     # pm sync 内部会做
python3 pm.py set F-2026-11-05 web doing --note "先按草案搭壳"
python3 pm.py build               # 本地看面板
python3 pm.py sync                # pull → build → commit → push
```

`pm sync` 在 pull 失败（离线）时会继续本地提交，下次再推——这是刻意设计的。

---

## 6. 数据模型

### `hub.json`（配置，改动少）

```json
{
  "name": "my-project",
  "current_iteration": "2026-11",
  "iterations": [{"id": "2026-11", "title": "11 月迭代", "start": "2026-11-02",
                  "end": "2026-11-29", "goal": "iOS 首发 + 故事分组 + 离线能力"}],
  "platforms": ["backend", "web", "ios", "android", "hos"],
  "machines": {"windows-home": {"label": "家用 Windows（日常）", "writable": true},
               "mac-weekend": {"label": "Mac（周末）", "writable": true},
               "cloud": {"label": "云服务器（只读聚合）", "writable": false}},
  "risk_lag_days": 7,
  "repos": {"server": {"name": "my-project", "role": "后端 + Web"},
            "clients": {"name": "my-app", "role": "iOS / Android / 鸿蒙"}}
}
```

### `features/F-2026-11-05.json`（需求定义）

```json
{
  "id": "F-2026-11-05",
  "title": "故事分组与持久化",
  "iteration": "2026-11",
  "priority": "P1",
  "area": "story",
  "spec": "chat-story.md §分组",
  "acceptance": ["分组在重启后仍存在", "旧数据可迁移"],
  "plan": {"doc": "chat-story.md §分组", "approved": true, "at": "2026-11-08"},
  "contract": {"version": "v1.11", "frozen": false, "doc": "docs/api-contract.md"},
  "platforms": {
    "backend": {"status": "todo", "owner": "me"},
    "web":     {"status": "todo", "owner": "me", "depends_on": ["F-2026-11-05:backend"]},
    "ios":     {"status": "todo", "owner": "me", "eta": "2026-11-22"},
    "android": {"status": "todo", "owner": "ai-1", "eta": "2026-11-20"},
    "hos":     {"status": "na"}
  }
}
```

- `plan`：方案闸门，未批准时任何泳道开工都会告警；
- `owner`：负责人（人 / AI Agent），来自 `hub.json` 的 `people`；
- `depends_on`：硬依赖（`需求:泳道`），只有它会阻塞任务；
- `platforms` 是历史命名，配置里也可写 `lanes`，二者等价。

### 事件（`events/<machine>/<YYYY-MM>.jsonl`，一行一条）

```json
{"id":"windows-home-2026-11-09T02:15:00Z-a1b2c3","ts":"2026-11-09T02:15:00Z","machine":"windows-home","type":"status","feature":"F-2026-11-05","platform":"web","value":"doing","note":"先按草案搭壳"}
{"id":"windows-home-2026-11-08T09:00:00Z-c9f1","ts":"2026-11-08T09:00:00Z","machine":"windows-home","type":"contract","feature":"F-2026-11-05","value":{"version":"v1.11","frozen":false,"doc":"docs/api-contract.md"}}
{"id":"mac-weekend-2026-11-08T07:20:00Z-77aa","ts":"2026-11-08T07:20:00Z","machine":"mac-weekend","type":"status","feature":"F-2026-11-01","platform":"ios","value":"doing","note":"登录 + 家庭页跑通"}
```

事件类型只有 4 种：`status` / `contract` / `note` / `feature`（元数据补丁）。
状态枚举固定 6 个：`todo` `doing` `blocked` `review` `done` `na`（na = 该需求不涉及此端）。

---

## 7. 契约先行：把「后端最快」变成优势

后端比前端快，正确的用法不是「让前端等」，也不是「前端猜接口」，而是：

```
后端写草案 → pm contract --version v1.11 --unfreeze
           → 冻结：pm contract --version v1.11
           → 前端才开工（否则面板红色告警）
```

**面板里最重要的不是进度条，而是这条规则：**
「契约未冻结，但 Web/iOS 已开工」= 红色。它比任何百分比都更能避免返工。

配套的零依赖 mock（阶段 1.5，可选）：

- `contracts/api.json`：OpenAPI 3 的 **JSON** 格式（不是 YAML，因为 Python 标准库直接能读）；
- `fixtures/*.json`：每个端点的示例响应；
- `pm mock --port 8788`：约 50 行 `http.server`，按路径返回 fixture。
  前端在后端不在线、契约刚冻结时就能并行开发，且不引入 Prism/WireMock 之类依赖。

契约版本策略：破坏性变更必须升版本 + 发 `contract` 事件 + 面板提示「已开工的端需回归」。
`backend-contract.md` 仍是契约正文（在 `my-project` 仓库），roadmap 只记录**版本号与冻结状态**，不复制正文。

---

## 8. 迭代节奏：按周末对齐，而不是按自然周

Mac 只有周末可用，所以时间盒要围着周末设计：

| 时间 | 动作 |
|---|---|
| 周五晚 | 冻结下周要做的接口契约（`pm contract`）；确认后端/Web 的可交付范围 |
| 周六–周日 | iOS 冲刺；Windows 侧继续 Android / 鸿蒙 / 后端；随时 `pm set` |
| 周日 22:00 | **迭代边界**：`pm sync` 收口，面板生成本周总结 |
| 周一 | 15 分钟看面板：只处理红色项（阻塞 / 契约 / 滞后） |

状态流水线：`todo → doing → review（真机验收）→ done`。
移动端可以再加一层 `released`（提审 / 上架）——建议作为阶段 2 的可选事件类型，
因为「代码完成」和「商店上架」在 iOS / 鸿蒙上能差一两周，混在一个状态里会失真。

---

## 9. 面板指标：只放能指导行动的

| 指标 | 定义 | 为什么 |
|---|---|---|
| 可发布功能 | 该需求所有涉及平台都是 `done`/`na` | 唯一有意义的「整体完成度」 |
| 各端进度 | 该端 `done` / 该端非 `na` 需求数 | 一眼看出最落后端 |
| 阻塞 | `blocked` 槽位数 + 原因 | 必须当天处理 |
| 契约未冻结 | 有版本、未冻结、且前端已开工 | 返工预警 |
| 滞后 | 后端 done 且移动端仍 todo，超过 7 天 | 移动端排期风险 |
| 最落后端 | 进度最低的端 | 周末该干什么 |
| 最新版采纳率 | 各端已开工任务中 pin 到最新 revision 的比例 | 衡量滞后程度，而不是"完成度" |
| 方案未批 | 已开工但方案未批准的需求数 | 方案先行闸门 |
| 依赖阻塞 | 依赖未完成的任务数 | 唯一会阻塞任务的硬约束 |
| 完成趋势 | 按事件时间累计 done | 看「这周有没有真的推进」 |
| 机器最近事件 | 每台机器最后一条事件时间 | 发现某台机器一周没同步 |

**刻意不做**：加权「综合完成度」百分比。它永远算不准，而且会让人盯着数字而不是红色项。

---

## 10. 技术选型：新增依赖 = 0

| 组件 | 选型 | 依赖 |
|---|---|---|
| 状态存储 | 文件 + git | 已有 |
| CLI / 折叠 / 生成 | Python 3 标准库（`json` `argparse` `http.server` `subprocess`） | 已有（项目里已有 Python 脚本） |
| 面板 | 单文件 HTML + 原生 JS + 内联 CSS，无 CDN、无框架 | 0 |
| 发布 | 云服务器 cron + 已有 openresty/nginx 静态目录 | 已有 |
| 备份 / 远端 | GitHub 私有仓库 | 已有 |
| 面板鉴权 | nginx `auth_basic`（只读页面，够用） | 已有 |
| 可选 | GitHub Actions（免费额度）做第二构建路径；systemd timer 替代 cron | 可选 |

面板 HTML 是完全自包含的：**断网、双击打开也能看**，这一点在 Windows 家里或 Mac 上很有用。

---

## 11. 落地步骤（约 30 分钟）

1. **建仓库**：GitHub 新建私有 `english-roadmap`，把本目录内容推上去（`out/` 已被忽略）。
2. **改 `hub.json`**：平台、迭代、机器、仓库信息换成自己的。
3. **导入存量**：把 `feature-index.md` 里约十几条功能一次性录成 `features/*.json`
   （可写个一次性脚本解析表格，或手工 `pm new`；只做一次）。
4. **Windows**：
   ```bash
   git clone git@github.com:ayouwish/english-roadmap.git
   cd english-roadmap && python pm.py machine windows-home
   python pm.py build
   ```
5. **Mac（周末）**：同样 clone，`python pm.py machine mac-weekend`，用完 `pm sync`。
6. **云服务器**：
   ```cron
   */5 * * * * cd /srv/english-roadmap && git pull -q --rebase && python3 pm.py build && rsync -a out/ /srv/www/hub/
   ```
   ```nginx
   location /hub/ {
       alias /srv/www/hub/;
       auth_basic "hub"; auth_basic_user_file /etc/nginx/hub.htpasswd;
   }
   ```
7. **可选**：`post-merge` hook 自动 `pm build`；commit trailer `Refs: F-…`。

---

## 12. 和现有文档的关系

| 现有文件 | 之后 |
|---|---|
| `my-app/docs/feature-index.md` | 不再手写；由 `pm build` 生成 `FEATURES.md` 并发布到面板同目录（一次性导入后删掉手写版） |
| `my-app/docs/backend-contract.md` | 不动，仍是契约正文；roadmap 只记版本/冻结状态 |
| `screens-matrix.md` / `chat-story.md` / `.specs/*` | 不动；`feature.spec` 指向它们 |
| `docs/release-notes.md` | 不动；发布时引用面板导出的迭代清单 |

这样文档只有两种：**人写的规格**（spec）和**机器生成的状态**（面板 / FEATURES.md），不会互相污染。

---

## 13. 备选方案对比

| 方案 | 多仓库聚合 | 离线 | 契约冻结字段 | 维护成本 | 手机可看 | 数据所有权 |
|---|---|---|---|---|---|---|
| **本方案**（git 文件 + 静态面板） | ✅ 自然 | ✅ | ✅ 自定义 | 极低 | ✅ | ✅ 自己的仓库 |
| GitHub Issues + Projects | ⚠️ 要跨仓库/手动同步 | ❌ | ⚠️ 只能塞 label/字段 | 低 | ✅ | ⚠️ 平台 |
| 自建 Web + SQLite | ✅ | ❌ 写入需在线 | ✅ | **高**（常驻服务/鉴权/备份/迁移） | ✅ | ✅ |
| Notion / 飞书多维表 | ⚠️ | ❌ | ⚠️ | 低 | ✅ | ❌ |
| 纯 Markdown（现状） | ❌ | ✅ | ⚠️ | 低但会漂移 | ❌ | ✅ |

GitHub Projects 是最接近的替代品，但它解决不了三个具体问题：
跨两个仓库汇总、Windows 离线时不能记状态、以及「契约冻结」这种领域字段没有地方放。

---

## 14. 反模式（不要做）

1. **把面板做成可写服务**——立刻需要常驻进程、鉴权、数据库、备份、冲突处理，个人开发得不偿失。
2. **双写**：既手改 `feature-index.md` 又改状态文件——两张皮会立刻回来。
3. **把 5 个端塞进一个仓库/分支**——构建环境（Xcode / DevEco / Android SDK / Node）互斥，这正是分仓库的原因。
4. **每台机器各自维护状态文件**——冲突的根源；状态必须只有一个写入路径（追加事件）。
5. **用 submodule 串三个仓库**——跨机更新是灾难，改成「ID 引用 + trailer」即可。
6. **过早引入 CI/CD 和自动部署**——现在手动部署 30 秒，自动化维护成本更高。
7. **用加权完成度骗自己**——只看红色项和「可发布数」。

---

## 15. 演进路线

| 阶段 | 内容 | 触发条件 |
|---|---|---|
| 1（现在） | 文件 + CLI + 静态面板 | — |
| 2 | commit trailer 解析（需求↔提交）、移动端 `released` 状态、`pm mock` | 面板开始不够用时 |
| 3 | GitHub Actions 构建校验 / 真机验证报告归档 | 需要跨机复现构建时 |
| 4 | SQLite + 小服务（或 GitHub API 同步） | **出现第二个人**或需要移动端写入时 |

---

## 16. 本目录内容

```
iteration-hub/
├── pm.py                 零依赖 CLI：scaffold / init / setup / projects / bind / new / set /
│                         change / plan / assign / dep / dispatch / next / task / report /
│                         queue / contract / log / list / build / gen / shots / check / sync /
│                         serve / doctor
├── hub.json              仓库级配置（projects / machines / people / default_project）
├── projects/<pid>/       项目级：hub.json + features + events + spec + design + shots
├── out/                  生成物（gitignore）：index.html + <pid>/dashboard.html 等
└── projects/shop-demo/   示例项目（生产数据放独立的 my-roadmap）
```

试跑：

```bash
python pm.py build        # 生成面板并打印终端摘要
python pm.py list         # 终端里的需求矩阵（含 revision 与漂移）
python pm.py check        # revision / 令牌 / 屏幕规格自检
python pm.py serve        # http://127.0.0.1:8787/dashboard.html
python pm.py doctor       # 自检
```

---

## 17. 通用化：一套工具，多个项目

平台（泳道）、负责人、机器、仓库全部来自 `hub.json`，`pm.py` 里没有写死任何项目名，所以：

- **新项目**：`pm scaffold --dir ../x-roadmap --name x --preset multi`，3 分钟得到一个可用的 hub；
- **多项目并存**：`pm --hub /path/to/hub <cmd>`（或 `PM_HUB` 环境变量），云服务器上一个 cron 刷新所有项目；
- **换机器**：`git clone` + `pm setup --machine <名字>`；
- **从旧项目迁移**：只录当前迭代的 10–15 条需求，代码仓库不动；
- **多人 / 多泳道**：`people` + `lanes` + `depends_on`，面板自带任务看板（方案 → 各泳道实现）。

参数语法、预设、多项目聚合、迁移步骤见 [GUIDE.md](GUIDE.md)。

---

## 18. 本地可写面板（日常不用记命令）

```
双击 start-pm.cmd / start-pm.command
        │
        ▼
localhost:8787（pm.py serve --open）
   GET  /             → 可写版面板（同一份 HTML，EDIT=true）
   GET  /api/meta     → 机器名 / 未同步项 / 最近提交
   POST /api/task     → 状态 + 负责人 + revision + ETA + 依赖 + 备注
   POST /api/plan     → 方案批准 / 撤销
   POST /api/change   → 新增 revision（破坏性标记）
   POST /api/feature  → 新建需求
   POST /api/sync     → pull → build → commit → push
        │
        ▼
events/<本机>/<月>.jsonl（只追加）→ build → 页面刷新
```

- **写操作只发生在本地**：浏览器 → localhost API → 追加本机事件文件，和 CLI 写的是同一份数据；
- **云端仍然只读**：静态 `dashboard.html`（EDIT=false）由 cron 生成，没有任何写接口；
- **多机不冲突**：每台机器只写自己的事件文件，同步按钮只是 pull/commit/push；
- **安全**：默认绑定 `127.0.0.1`；非本机地址必须 `--read-only`，不会把写 API 暴露到局域网；
- **启动器**：`pm setup` 生成 `start-pm.cmd`（Windows 双击）和 `start-pm.command`（Mac 双击），
  打开即用、关掉即停，不需要常驻服务；
- **CLI 保留**：批量导入、脚本、云端 cron、`pm shots --check` 等仍然用命令，日常 90% 操作在页面上完成。

为什么不把写操作放到云端：需要鉴权、常驻进程、并发写合并、备份，个人开发维护不起；
而"本地写 + 事件追加 + 手动/自动同步"已经覆盖全部日常，并且保留了离线能力。

---

## 19. DSH 会话协议（会话是执行主体）

```
人类：面板上派工 / 看全局 / 同步
        │  dispatch 事件（任务进队列）
        ▼
DSH 会话：pm next / pm task F-1:ios
        │  claim 事件：自动 doing + 记录 DSH_SESSION_ID
        ▼
        │  pm report --status review|done --rev rN
        ▼
面板：卡片显示「待认领 / 会话进行中 / 完成」
```

- **需求由会话提交/修改**：`pm new` / `pm change` / `pm plan`，会话是需求的作者；
- **任务由会话认领**：`pm next` 优先领队列里最早的派工，队列空则领最高优先级可开工任务；
  `pm task <ID>:<lane>` 领指定任务；领取即上报（doing + session）；
- **进度由会话上报**：`pm report` 自动带 `DSH_SESSION_ID`，本会话只有一个任务时可省略任务 ID；
- **人类只做三件事**：派工、看板、同步；不再手动改状态；
- **为什么不从面板直接"启动"会话**：DSH 的 `dsh://` 深链目前只支持 `dsh://open` 聚焦窗口，
  没有外部创建会话的公开接口；"队列 + 会话认领"是唯一不依赖私有 API 的可靠方式。
  如果以后 DSH 开放本地 API，只需把 `/api/dispatch` 的落点从队列换成"创建会话"即可，协议不变；
- **会话与任务绑定**：`cell.session` 记录会话 id；同一任务被重新认领会保留历史；
  多会话并发做不同任务互不影响。

### Linux

- 桌面：`./start-pm.sh`（`pm setup` 生成并 chmod +x）；
- 无 GUI 服务器：`pm serve --read-only` 交给 nginx；需要写面板时用
  `ssh -L 8787:127.0.0.1:8787 server` 再在本地打开；
- 云端 cron 仍是 `git pull && python3 pm.py build && rsync out/`。

---

## 20. 多项目：一个 pm，多个项目

生产上只维护**一个 pm 仓库**，项目是配置 + 数据目录：

```
hub.json                          # 仓库级：name / machines / people / projects / default_project
projects/<pid>/hub.json           # 项目级：lanes / iterations / repos / 阈值 / 令牌与规格路径
projects/<pid>/features/*.json    # 需求（项目内唯一；跨项目 ID 全局唯一）
projects/<pid>/events/<machine>/  # 事件（每机每月一个文件）
projects/<pid>/spec/screens/      # 屏幕规格
projects/<pid>/design/tokens.json # 设计令牌
projects/<pid>/shots/             # 截图基线
out/index.html                    # 项目总览
out/<pid>/dashboard.html          # 项目看板（+ state.json / FEATURES.md / generated / screens）
```

- **数据隔离**：项目之间不共享需求/事件，泳道和迭代各自配置；
- **会话绑定**：`.pm/sessions/<DSH_SESSION_ID>.json`（gitignore），`pm bind <pid>` 一次，
  之后 `pm next` / `pm task` / `pm report` 都在该项目内；
- **项目解析顺序**：`--project` / `PM_PROJECT` → 需求 ID 定位（ID 全局唯一）→ 会话绑定 → `default_project`；
- **面板**：`/` 项目总览，`/p/<pid>` 项目看板；云端静态 `out/` 结构一致（`index.html` + `<pid>/dashboard.html`）；
- **加项目**：复制 `projects/<pid>/` + 在顶层 `hub.json` 的 `projects` 登记；
- **为什么不是"一个仓库一个项目"**：机器、负责人、面板、启动器、同步流程都只维护一份；
  项目只是配置和数据目录，新增项目不需要新仓库/新部署。

---

## 21. 地址与远程模式

两种部署形态，同一套代码：

| 模式 | 状态在哪 | 谁写 | 适合 |
|---|---|---|---|
| 本地模式（默认） | 每台机器的 git 工作副本 | 本机 `pm` / 本地面板 | 离线、单机、git 即备份 |
| 远程模式 | 服务器上的 pm 仓库 | 服务器进程（加锁串行） | 多台机器/多个会话共享一个实例 |

**地址配置**

- 服务端：`pm serve --host 0.0.0.0 --token <secret>`（或 `PM_TOKEN`）；
- 客户端：`pm connect <url> --token <secret>` 写入 `~/.pm/client.json`；
- 临时：`--server` / `PM_URL` / `PM_TOKEN`；`--local` 强制本地；
- 优先级：`--server` > `PM_URL` > client.json > 本地模式。

**远程模式支持的命令**：`next / task / report / dispatch / queue / projects / bind`
（通过 `/api/claim`、`/api/report`、`/api/dispatch`、`/api/queue`、`/api/meta`）。
`build / sync / serve / init / scaffold / setup / gen / shots / check` 仍在服务器本地执行。

**安全**

- 远程写 API 必须 token（`Authorization: Bearer`）；没 token 时 `serve` 拒绝绑定非本机地址；
- 面板页面建议放在 nginx basic auth + HTTPS 后面；页面由服务端注入 token 供 JS 调 API；
- 只读展示可以 `--read-only` 且不设 token（或干脆用云端静态 `out/`）。

**远程模式的取舍**：写入串行化在服务器进程内（`threading.Lock`），事件仍按机器分文件；
git 提交由服务器统一做。代价是断网不能写——需要离线时用本地模式，回到网络再 `pm sync`。

---

## 22. 三个仓库的职责

```
pm 工具仓                      roadmap 数据仓                 子项目代码仓
pm.py  ──--roadmap 指向──▶     hub.json + projects/<pid>/  ◀──repos 引用──  my-project / app / …
（升级只需换 pm.py）            （git 即备份，push 到 GitHub）   （pm 不读不写代码）
```

- **pm 工具仓**：只放工具与文档，可独立升级；不包含任何项目数据；
- **roadmap 数据仓**：`pm scaffold` 生成；`hub.json` 里登记 projects/machines/people，
  每个项目在 `projects/<pid>/` 下有自己的配置、需求、事件、规格、令牌、截图；
- **子项目代码仓**：在 `projects/<pid>/hub.json` 的 `repos` 里登记（只引用，不拉代码）；
  DSH 会话领任务时简报会带上仓库名，代码 commit 在各自仓库完成，pm 只记录进度。

**绑定关系**

1. 工具 → 数据：`pm roadmap <dir>`（默认）或 `--roadmap <dir>`（临时）/ `PM_ROADMAP`；
2. 数据 → 代码：项目配置 `repos`；
3. 会话 → 项目：`pm bind <pid>`（每个会话一次）；
4. 远程：`pm connect <url> --token`，之后命令走远程 API。

**为什么拆开**：工具升级不动数据；数据仓小、可随时 clone/备份；代码仓按平台独立构建；
三者生命周期不同，混在一起会导致"升级工具污染数据""数据仓过大""跨端构建冲突"。
