# 在 DSH 会话里操作这个仓库

你是执行主体：人类在面板上派工、看全局；你通过 `pm` 命令领任务、上报、改需求。
**不要手改 `events/` 下的文件**，所有状态变更都走命令（追加事件，多机不冲突）。

## 绑定项目

```bash
pm projects                      # 看有哪些项目、当前会话绑了哪个
pm bind shop-demo                # 每个会话绑定一次
pm bind                          # 查看当前绑定
```

之后 `pm next` / `pm task` / `pm report` 都在这个项目里操作。
临时切项目：`pm next --project shop-demo`，或 `pm task shop-demo/S-2:web`。

## 领任务

```bash
pm next                          # 领取队列里最早的派工；队列空则领最高优先级的可开工任务
pm task F-2026-11-02:ios         # 领取指定任务（也支持 pm task F-2026-11-02 ios）
pm task F-2026-11-02             # 该需求只有一个未完成泳道时可省略泳道
```

领取后会自动把任务标为进行中，并记录你的 `DSH_SESSION_ID`（环境变量自动读取）。
命令会打印任务简报：方案、规格、契约、验收、屏幕规格路径、工作约定。

## 上报进度

```bash
pm report --status doing --note "开工"          # 本会话只有一个任务时可省略任务 ID
pm report --status review --note "自测通过"
pm report --status done --rev r2 --note "完成"  # 完成必须带 --rev
pm report --status blocked --blocked-by "等契约 v1.11"
```

## 提交 / 修改需求

```bash
pm new --id F-2026-11-15 --title "新需求" --priority P1
pm plan F-2026-11-15 --doc docs/xxx.md --approve
pm change F-2026-11-15 --rev r2 --breaking --note "变更原因"
```

## 收尾

```bash
pm check     # 方案/依赖/负责人/revision/规格自检
pm sync      # pull → build → commit → push
```

## 规则

- 一个会话同一时间只做一个任务，避免跨任务上下文污染；
- 完成前跑 `pm check`，不要跳过；
- 需求变了用 `pm change`（新增 revision），不要重开需求；
- 需要人类决策时，`pm report --status blocked --blocked-by "..."` 并在对话里说明。
