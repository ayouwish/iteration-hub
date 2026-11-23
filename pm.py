#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pm.py —— 个人多端迭代管理器 / 开发面板生成器

零第三方依赖：只用 Python 3 标准库 + git + 任意静态文件服务器。

一个 pm 仓库（建议独立命名，例如 english-roadmap）就是「需求的真相源」：
  hub.json                          配置：平台 / 迭代 / 机器 / 告警阈值
  features/<FEAT-ID>.json           需求定义（创建后很少改，每个需求一个文件）
  events/<machine>/<YYYY-MM>.jsonl  只追加事件日志（每台机器每月一个文件 → 结构上永不冲突）
  out/state.json                    折叠后的状态快照（生成物，不入库）
  out/dashboard.html                单文件开发面板（生成物，不入库）
  out/FEATURES.md                   生成的功能索引（生成物，不入库）

常用命令：
  python pm.py init                        # 初始化目录与默认配置
  python pm.py machine mac-weekend         # 记录本机身份（写入 .machine，不入库）
  python pm.py new  --id F-2026-11-01 --title "..." --priority P0
  python pm.py set  F-2026-11-01 web doing --eta 2026-11-15
  python pm.py set  F-2026-11-01 android blocked --blocked-by "等契约 v1.11"
  python pm.py contract F-2026-11-05 --version v1.11 --doc docs/api-contract.md
  python pm.py log  F-2026-11-01 "iOS 登录流程跑通"
  python pm.py list [--iter 2026-11] [--platform ios]
  python pm.py build                       # 折叠事件 → 面板 / 文档 / 快照
  python pm.py sync                        # pull → build → commit → push
  python pm.py serve --port 8787           # 本地预览面板
  python pm.py doctor                      # 自检
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import unicodedata
import webbrowser
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUSES = ("todo", "doing", "blocked", "review", "done", "na")
DONE_STATES = ("done", "na")
GLYPH = {"todo": "·", "doing": "◐", "blocked": "✕", "review": "◑", "done": "✓", "na": "—"}
SHORT = {"backend": "后", "web": "Web", "ios": "iOS", "android": "And", "hos": "鸿"}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

DEFAULT_CFG = {
    "name": "roadmap",
    "current_iteration": "2026-11",
    "iterations": [],
    "platforms": ["backend", "web", "ios", "android", "hos"],
    "platform_labels": {
        "backend": "后端",
        "web": "Web",
        "ios": "iOS",
        "android": "Android",
        "hos": "鸿蒙",
    },
    "machines": {},
    "machine_aliases": {},
    "risk_lag_days": 7,
    "stale_days": 5,
    "repos": {},
    "platform_short": {},
    "platform_colors": {},
    "mobile_platforms": ["ios", "android", "hos"],
    "tokens_path": "design/tokens.json",
    "screens_dir": "spec/screens",
    "shots_dir": "shots",
    "people": {},
    "lane_order": [],
    "projects": {},
    "default_project": None,
}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def die(msg: str, code: int = 1):
    print(f"错误: {msg}", file=sys.stderr)
    raise SystemExit(code)


def warn(msg: str):
    print(f"警告: {msg}", file=sys.stderr)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def w(text: str) -> int:
    """终端显示宽度（CJK 算 2）"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))


def pad(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - w(text))


def sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))


def short_label(cfg: dict, p: str) -> str:
    short = cfg.get("platform_short") or {}
    return short.get(p) or (cfg.get("platform_labels") or {}).get(p, p)


def palette(cfg: dict) -> dict:
    """平台 → 颜色；未配置的按固定色板循环分配。"""
    colors = dict(cfg.get("platform_colors") or {})
    fallback = ["#34d399", "#38bdf8", "#c084fc", "#4ade80", "#fb923c", "#f472b6", "#facc15"]
    for i, p in enumerate(cfg.get("platforms") or []):
        colors.setdefault(p, fallback[i % len(fallback)])
    return colors


# --------------------------------------------------------------------------
# 配置 / 布局 / 事件
# --------------------------------------------------------------------------
TOOL_DIR = Path(__file__).resolve().parent
HUB_ROOT = TOOL_DIR      # roadmap 数据仓根（由 --roadmap / pm roadmap 指定）
ROOT = HUB_ROOT          # 当前项目根目录（由 use_project 切换）
CURRENT_PID = None


def load_hub() -> dict:
    p = HUB_ROOT / "hub.json"
    if not p.exists():
        die(f"缺少 hub.json（{p}）。用 --roadmap 指向 roadmap 仓库，或 `pm roadmap <path>` 设置默认。")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"hub.json 不是合法 JSON: {e}")
    projects = raw.get("projects")
    if not projects:
        name = raw.get("name") or HUB_ROOT.name
        projects = {name: {"name": name, "path": "."}}
        default = name
    else:
        projects = {
            str(pid): {"name": (meta or {}).get("name", pid),
                       "path": (meta or {}).get("path", f"projects/{pid}")}
            for pid, meta in projects.items()
        }
        default = raw.get("default_project") or next(iter(projects))
    if default not in projects:
        die(f"default_project 不在 projects 里: {default}")
    return {
        "name": raw.get("name") or HUB_ROOT.name,
        "machines": raw.get("machines") or {},
        "people": raw.get("people") or {},
        "machine_aliases": raw.get("machine_aliases") or {},
        "projects": projects,
        "default_project": default,
    }


def project_dir(pid: str) -> Path:
    hub = load_hub()
    if pid not in hub["projects"]:
        die(f"未知项目: {pid}（可用: {', '.join(hub['projects'])}）")
    return (HUB_ROOT / hub["projects"][pid]["path"]).resolve()


def use_project(pid: str):
    global ROOT, CURRENT_PID
    ROOT = project_dir(pid)
    CURRENT_PID = pid
    ensure_layout()


def ensure_layout():
    for d in ("features", "events", "out", "spec/screens", "design", "shots"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)


def out_dir() -> Path:
    pid = CURRENT_PID or "default"
    d = HUB_ROOT / "out" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_cfg() -> dict:
    p = ROOT / "hub.json"
    if not p.exists():
        die(f"缺少项目配置: {p}（可用 `pm scaffold` 生成，或手工放一份）")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{p} 不是合法 JSON: {e}")
    cfg = dict(DEFAULT_CFG)
    cfg.update(raw or {})
    hub = load_hub()
    cfg["hub_name"] = hub["name"]
    cfg["project"] = CURRENT_PID or hub["default_project"]
    cfg["projects"] = hub["projects"]
    cfg["default_project"] = hub["default_project"]
    for k in ("machines", "people", "machine_aliases"):
        if not cfg.get(k):
            cfg[k] = hub.get(k) or {}
    lanes = raw.get("lanes") or raw.get("platforms") or DEFAULT_CFG["platforms"]
    cfg["lanes"] = list(lanes)
    cfg["platforms"] = list(lanes)  # 兼容旧代码/旧配置
    labels = raw.get("lane_labels") or raw.get("platform_labels") or DEFAULT_CFG["platform_labels"]
    cfg["lane_labels"] = labels
    cfg["platform_labels"] = labels
    short = raw.get("lane_short") or raw.get("platform_short") or {}
    cfg["lane_short"] = short
    cfg["platform_short"] = short
    colors = raw.get("lane_colors") or raw.get("platform_colors") or {}
    cfg["lane_colors"] = colors
    cfg["platform_colors"] = colors
    if not cfg["platforms"]:
        die(f"{p} 的 lanes 不能为空")
    if not cfg.get("iterations"):
        cfg["iterations"] = [{"id": cfg["current_iteration"],
                              "title": cfg["current_iteration"],
                              "start": None, "end": None, "goal": ""}]
    return cfg


def machine_name(cfg: dict) -> str:
    env = os.environ.get("PM_MACHINE", "").strip()
    if env:
        return sanitize(env)
    f = HUB_ROOT / ".machine"
    if f.exists():
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return sanitize(v)
    host = socket.gethostname().split(".")[0]
    aliases = cfg.get("machine_aliases") or {}
    return sanitize(aliases.get(host, host.lower()))


def append_event(ev: dict):
    d = ROOT / "events" / sanitize(ev["machine"])
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ev['ts'][:7]}.jsonl"
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def load_features() -> dict:
    feats = {}
    for p in sorted((ROOT / "features").glob("*.json")):
        try:
            f = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"{p} 不是合法 JSON: {e}")
        f.setdefault("id", p.stem)
        f.setdefault("project", CURRENT_PID)
        f.setdefault("platforms", {})
        f.setdefault("contract", {})
        f.setdefault("acceptance", [])
        if f["id"] in feats:
            die(f"需求 ID 重复: {f['id']}")
        feats[f["id"]] = f
    return feats


def read_events() -> list:
    evs, bad = [], 0
    for p in sorted((ROOT / "events").glob("*/*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            warn(f"读不到 {p}: {e}")
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            ev.setdefault("id", f"{p.parent.name}-{p.stem}-{len(evs)}")
            ev.setdefault("machine", p.parent.name)
            evs.append(ev)
    if bad:
        warn(f"跳过 {bad} 行无法解析的事件（可能正在写入，重跑即可）")
    seen, uniq = set(), []
    for ev in evs:
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        uniq.append(ev)
    uniq.sort(key=lambda e: (str(e.get("ts") or ""), str(e.get("machine") or ""), str(e.get("id") or "")))
    return uniq


def machine_id() -> str:
    f = HUB_ROOT / ".machine"
    if f.exists():
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return sanitize(v)
    hub = load_hub()
    host = socket.gethostname().split(".")[0]
    aliases = hub.get("machine_aliases") or {}
    return sanitize(aliases.get(host, host.lower()))


def session_id() -> str:
    sid = os.environ.get("DSH_SESSION_ID", "").strip()
    return sanitize(sid) if sid else machine_id()


def pm_home() -> Path:
    env = os.environ.get("PM_HOME", "").strip()
    if env:
        d = Path(env).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
    try:
        d = Path.home() / ".pm"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        d = TOOL_DIR / ".pm"
        d.mkdir(parents=True, exist_ok=True)
        return d


def bind_path(sess: str) -> Path:
    d = pm_home() / "bindings"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sanitize(sess)}.json"


def bound_project(hub: dict, sess: str):
    p = bind_path(sess)
    if not p.exists():
        return None
    try:
        pid = json.loads(p.read_text(encoding="utf-8")).get("project")
    except json.JSONDecodeError:
        return None
    return pid if pid in hub["projects"] else None


def find_project_of_feature(hub: dict, fid: str) -> list:
    return [pid for pid in hub["projects"]
            if (project_dir(pid) / "features" / f"{fid}.json").exists()]


def resolve_project(hub: dict, arg=None, feature=None) -> str:
    if arg:
        if arg not in hub["projects"]:
            die(f"未知项目: {arg}（可用: {', '.join(hub['projects'])}）")
        return arg
    env = os.environ.get("PM_PROJECT", "").strip()
    if env:
        if env not in hub["projects"]:
            die(f"PM_PROJECT={env} 不在 projects 里")
        return env
    if feature:
        hits = find_project_of_feature(hub, feature)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            die(f"{feature} 在多个项目里（{', '.join(hits)}），请用 --project 指定")
    pid = bound_project(hub, session_id())
    if pid:
        return pid
    return hub["default_project"]


# 折叠：事件 → 当前状态
# --------------------------------------------------------------------------
def fold(cfg: dict, feats: dict, events: list) -> dict:
    state = {}
    for fid, f in feats.items():
        plats = {}
        for p in cfg["platforms"]:
            src = (f.get("platforms") or {}).get(p) or {}
            if isinstance(src, str):
                src = {"status": src}
            plats[p] = {
                "status": src.get("status", "na"),
                "eta": src.get("eta"),
                "owner": src.get("owner"),
                "blocked_by": src.get("blocked_by"),
                "note": src.get("note"),
                "rev": src.get("rev"),
                "depends_on": list(src.get("depends_on") or []),
                "session": src.get("session"),
                "dispatch": src.get("dispatch"),
                "updated_at": src.get("updated_at"),
                "updated_by": src.get("updated_by"),
            }
        state[fid] = {
            "feature": f,
            "platforms": plats,
            "notes": [],
            "contract": dict(f.get("contract") or {}),
            "last_event": None,
        }
    for ev in events:
        s = state.get(ev.get("feature"))
        if s is None:
            continue
        t = ev.get("type")
        if t == "status":
            cell = s["platforms"].get(ev.get("platform"))
            if cell is None:
                continue
            if ev.get("value") in STATUSES:
                cell["status"] = ev["value"]
            for k in ("eta", "owner", "blocked_by", "note", "rev", "session"):
                if ev.get(k) is not None:
                    cell[k] = ev[k]
            cell["updated_at"] = ev.get("ts")
            cell["updated_by"] = ev.get("machine")
        elif t == "dispatch":
            cell = s["platforms"].get(ev.get("platform"))
            if cell is not None:
                cell["dispatch"] = dict(ev.get("value") or {}, state="queued")
        elif t == "claim":
            cell = s["platforms"].get(ev.get("platform"))
            if cell is not None:
                v = dict(ev.get("value") or {})
                cell["session"] = v.get("session")
                cell["dispatch"] = dict(cell.get("dispatch") or {}, state="claimed", **v)
                if cell["status"] == "todo":
                    cell["status"] = "doing"
                cell["updated_at"] = ev.get("ts")
                cell["updated_by"] = ev.get("machine")
        elif t == "change":
            v = ev.get("value") or {}
            rev = v.get("rev")
            if rev:
                s["feature"]["revision"] = rev
                revs = s["feature"].setdefault("revisions", [])
                if not any(r.get("rev") == rev for r in revs):
                    revs.append(v)
        elif t == "plan":
            s["feature"]["plan"] = dict(ev.get("value") or {})
            s["feature"]["plan"]["updated_at"] = ev.get("ts")
        elif t == "assign":
            cell = s["platforms"].get(ev.get("platform"))
            if cell is not None:
                cell["owner"] = ev.get("value")
        elif t == "dep":
            cell = s["platforms"].get(ev.get("platform"))
            if cell is not None:
                cell["depends_on"] = list(ev.get("value") or [])
        elif t == "contract":
            s["contract"] = dict(ev.get("value") or {})
            s["contract"]["updated_at"] = ev.get("ts")
        elif t == "note":
            s["notes"].append(
                {"ts": ev.get("ts"), "machine": ev.get("machine"), "text": ev.get("value")}
            )
        elif t == "feature":
            s["feature"].update(ev.get("value") or {})
        s["last_event"] = ev
    for s in state.values():
        s["notes"] = s["notes"][-5:]
    return state


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------
def feat_class(s: dict, plats) -> str:
    sts = [s["platforms"][p]["status"] for p in plats]
    if all(x in DONE_STATES for x in sts):
        return "release"
    if any(x == "blocked" for x in sts):
        return "blocked"
    if any(x in ("doing", "review", "done") for x in sts):
        return "doing"
    return "todo"


def compute(cfg: dict, state: dict, events: list) -> dict:
    plats = cfg["platforms"]
    labels = cfg.get("platform_labels") or {}
    it = cfg["current_iteration"]
    now = datetime.now(timezone.utc)

    in_iter = {fid: s for fid, s in state.items() if s["feature"].get("iteration") == it}
    backlog = sum(1 for s in state.values() if s["feature"].get("iteration") == "backlog")

    per_platform = {}
    for p in plats:
        c = Counter(s["platforms"][p]["status"] for s in in_iter.values())
        total = sum(c[x] for x in STATUSES if x != "na")
        per_platform[p] = {
            "label": labels.get(p, p),
            "counts": {x: c[x] for x in STATUSES},
            "total": total,
            "done": c["done"],
            "pct": round(100 * c["done"] / total) if total else 0,
        }

    by_status = Counter(feat_class(s, plats) for s in in_iter.values())
    blocked_slots = sum(
        1 for s in in_iter.values() for p in plats if s["platforms"][p]["status"] == "blocked"
    )

    # 完成时间（用于趋势图和滞后判断）
    done_dt = {}
    for ev in events:
        if ev.get("type") == "status" and ev.get("value") == "done":
            k = (ev.get("feature"), ev.get("platform"))
            dt = parse_ts(ev.get("ts"))
            if dt and (k not in done_dt or dt > done_dt[k]):
                done_dt[k] = dt

    itinfo = next((i for i in cfg["iterations"] if i["id"] == it), {})
    start = date.fromisoformat(itinfo["start"]) if itinfo.get("start") else now.date()
    today = now.date()
    if start > today:
        start = today
    days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)

    def rel_dt(s):
        dts = []
        for p in plats:
            if s["platforms"][p]["status"] == "done":
                dts.append(done_dt.get((s["feature"]["id"], p)) or start_dt)
        return max(dts) if dts else None

    series = {}
    for p in plats:
        arr = []
        for d in days:
            end = datetime.combine(d, time.max, tzinfo=timezone.utc)
            n = 0
            for s in in_iter.values():
                if s["platforms"][p]["status"] != "done":
                    continue
                dt = done_dt.get((s["feature"]["id"], p))
                if dt is None or dt <= end:
                    n += 1
            arr.append(n)
        series[p] = arr
    rel_arr = []
    for d in days:
        end = datetime.combine(d, time.max, tzinfo=timezone.utc)
        rel_arr.append(
            sum(1 for s in in_iter.values() if rel_dt(s) is not None and rel_dt(s) <= end)
        )
    series["release"] = rel_arr

    # 风险
    risks = []
    frontend = [p for p in plats if p != "backend"]
    contract_open = 0
    for fid, s in in_iter.items():
        ct = s["contract"] or {}
        started = [p for p in frontend if s["platforms"][p]["status"] in ("doing", "review", "done")]
        if ct.get("version") and not ct.get("frozen") and started:
            contract_open += 1
            risks.append(
                {
                    "level": "high",
                    "kind": "契约",
                    "feature": fid,
                    "text": f"{ct.get('version')} 契约未冻结，但 "
                    + " / ".join(labels.get(p, p) for p in started)
                    + " 已开工",
                }
            )
        for p in plats:
            cell = s["platforms"][p]
            if cell["status"] == "blocked":
                risks.append(
                    {
                        "level": "high",
                        "kind": "阻塞",
                        "feature": fid,
                        "text": f"{labels.get(p, p)} 被阻塞：{cell.get('blocked_by') or '未填写原因'}",
                    }
                )
        b = s["platforms"].get("backend")
        if b and b["status"] == "done":
            bdt = done_dt.get((fid, "backend"))
            if bdt:
                age = (now - bdt).days
                lag = [
                    p
                    for p in (cfg.get("mobile_platforms") or [])
                    if p in plats and s["platforms"][p]["status"] == "todo"
                ]
                if lag and age >= cfg.get("risk_lag_days", 7):
                    risks.append(
                        {
                            "level": "mid",
                            "kind": "滞后",
                            "feature": fid,
                            "text": f"后端已完成 {age} 天，"
                            + " / ".join(labels.get(p, p) for p in lag)
                            + " 仍未开始",
                        }
                    )

    drift = []
    for fid, s in in_iter.items():
        f = s["feature"]
        revs = f.get("revisions") or []
        cur = f.get("revision") or (revs[-1].get("rev") if revs else None)
        if not cur or len(revs) <= 1:
            continue
        order = [r.get("rev") for r in revs]
        for p in plats:
            cell = s["platforms"][p]
            if cell["status"] not in ("doing", "review", "done"):
                continue
            impl = cell.get("rev") or (order[0] if order else None)
            if impl == cur:
                continue
            later = order[order.index(impl) + 1:] if impl in order else order
            breaking = any(r.get("breaking") for r in revs if r.get("rev") in later)
            impact = "breaking" if breaking else "compatible"
            drift.append({"feature": fid, "platform": p, "impl": impl, "cur": cur,
                          "impact": impact})
            risks.append(
                {
                    "level": "high" if breaking else "mid",
                    "kind": "漂移",
                    "feature": fid,
                    "text": f"{labels.get(p, p)} 实现到 {impl}，当前 {cur}"
                    + ("（破坏性：需尽快跟进）" if breaking else "（兼容/优化：可择期跟进）"),
                }
            )

    # 方案闸门 + 任务（plan × lane）+ 依赖
    order_lanes = [p for p in (cfg.get("lane_order") or plats) if p in plats]
    for p in plats:
        if p not in order_lanes:
            order_lanes.append(p)
    status_map = {}
    for fid, s in in_iter.items():
        for p in plats:
            status_map[f"{fid}:{p}"] = s["platforms"][p]["status"]

    def plan_ok(s):
        return (s["feature"].get("plan") or {}).get("approved", True)

    plan_open = 0
    for fid, s in in_iter.items():
        started = [p for p in plats if s["platforms"][p]["status"] in ("doing", "review", "done")]
        if started and not plan_ok(s):
            plan_open += 1
            risks.append(
                {
                    "level": "high",
                    "kind": "方案",
                    "feature": fid,
                    "text": "方案未批准，但 "
                    + " / ".join(labels.get(p, p) for p in started)
                    + " 已开工",
                }
            )

    tasks = []
    for fid, s in in_iter.items():
        f = s["feature"]
        for p in plats:
            cell = s["platforms"][p]
            if cell["status"] == "na":
                continue
            deps = [d for d in (cell.get("depends_on") or []) if d in status_map]
            unmet = [d for d in deps if status_map.get(d) not in DONE_STATES]
            if cell["status"] in ("doing", "review", "done") and unmet:
                risks.append(
                    {
                        "level": "mid",
                        "kind": "依赖",
                        "feature": fid,
                        "text": f"{labels.get(p, p)} 已开工，但依赖 "
                        + " / ".join(unmet)
                        + " 未完成",
                    }
                )
            tasks.append(
                {
                    "key": f"{fid}:{p}",
                    "feature": fid,
                    "title": f.get("title", ""),
                    "lane": p,
                    "status": cell["status"],
                    "owner": cell.get("owner"),
                    "rev": cell.get("rev"),
                    "eta": cell.get("eta"),
                    "depends_on": deps,
                    "unmet": unmet,
                    "ready": (cell["status"] == "todo" and not unmet and plan_ok(s)),
                    "session": cell.get("session"),
                    "dispatch": (cell.get("dispatch") or {}).get("state")
                    or ("claimed" if cell.get("session") else None),
                    "dispatched_at": (cell.get("dispatch") or {}).get("at"),
                }
            )

    queued = sum(
        1 for t in tasks if t["dispatch"] == "queued" and t["status"] in ("todo", "doing")
    )
    claimed = sum(1 for t in tasks if t["session"])
    for t in tasks:
        if t["dispatch"] != "queued" or t["status"] not in ("todo", "doing"):
            continue
        dt = parse_ts(t.get("dispatched_at"))
        if dt and (now - dt).days >= cfg.get("queue_stale_days", 2):
            risks.append(
                {
                    "level": "mid",
                    "kind": "派工",
                    "feature": t["feature"],
                    "text": f"{labels.get(t['lane'], t['lane'])} 已派工 "
                    f"{(now - dt).days} 天仍未认领",
                }
            )

    dep_blocked = sum(1 for t in tasks if t["unmet"])
    ready = sum(1 for t in tasks if t["ready"])
    by_owner = Counter(
        t["owner"] or "未指派"
        for t in tasks
        if t["status"] in ("doing", "review", "blocked")
    )
    people = cfg.get("people") or {}
    owners = [
        {"id": oid, "name": (people.get(oid) or {}).get("name", oid), "count": n}
        for oid, n in by_owner.most_common()
    ]

    adoption = {}
    for p in plats:
        started = [
            s
            for s in in_iter.values()
            if s["platforms"][p]["status"] in ("doing", "review", "done")
        ]
        ok = 0
        for s in started:
            f = s["feature"]
            revs = f.get("revisions") or []
            cur = f.get("revision") or (revs[-1].get("rev") if revs else None)
            impl = s["platforms"][p].get("rev") or (revs[0].get("rev") if revs else None)
            if not cur or impl == cur:
                ok += 1
        adoption[p] = {
            "ok": ok,
            "total": len(started),
            "pct": round(100 * ok / len(started)) if started else None,
        }
    ad_total = sum(a["total"] for a in adoption.values())
    ad_ok = sum(a["ok"] for a in adoption.values())
    adoption_pct = round(100 * ad_ok / ad_total) if ad_total else 0

    last_seen = {}
    for ev in events:
        m, ts = ev.get("machine"), ev.get("ts")
        if m and ts and (m not in last_seen or ts > last_seen[m]):
            last_seen[m] = ts
    for m, info in (cfg.get("machines") or {}).items():
        if not (info or {}).get("writable", True):
            continue
        ts = last_seen.get(m)
        if not ts:
            continue
        dt = parse_ts(ts)
        if dt and (now - dt).days > cfg.get("stale_days", 5):
            risks.append(
                {
                    "level": "low",
                    "kind": "机器",
                    "feature": None,
                    "text": f"{info.get('label', m)} 已 {(now - dt).days} 天没有同步事件",
                }
            )

    lvl = {"high": 0, "mid": 1, "low": 2}
    risks.sort(key=lambda r: (lvl.get(r["level"], 3), r.get("feature") or ""))

    # 下一步建议
    actions = []
    for fid, s in in_iter.items():
        f = s["feature"]
        for p in plats:
            cell = s["platforms"][p]
            if cell["status"] in ("blocked", "doing", "review"):
                actions.append(
                    {
                        "id": fid,
                        "title": f.get("title", ""),
                        "platform": p,
                        "status": cell["status"],
                        "priority": f.get("priority", "P9"),
                        "eta": cell.get("eta"),
                    }
                )
    actions.sort(
        key=lambda a: (
            {"blocked": 0, "doing": 1, "review": 2}.get(a["status"], 3),
            PRIORITY_ORDER.get(a["priority"], 9),
            a["id"],
        )
    )

    return {
        "total": len(in_iter),
        "releasable": sorted(
            fid for fid, s in in_iter.items() if feat_class(s, plats) == "release"
        ),
        "by_status": {
            "todo": by_status.get("todo", 0),
            "doing": by_status.get("doing", 0),
            "blocked": by_status.get("blocked", 0),
            "release": by_status.get("release", 0),
        },
        "blocked_slots": blocked_slots,
        "contract_open": contract_open,
        "drift": len(drift),
        "drift_breaking": sum(1 for d in drift if d["impact"] == "breaking"),
        "plan_open": plan_open,
        "dep_blocked": dep_blocked,
        "ready": ready,
        "queued": queued,
        "claimed": claimed,
        "tasks": tasks,
        "owners": owners,
        "adoption": adoption,
        "adoption_pct": adoption_pct,
        "lane_order": order_lanes,
        "per_platform": per_platform,
        "risks": risks,
        "timeline": {"days": [d.isoformat() for d in days], "series": series},
        "next_actions": actions[:8],
        "backlog": backlog,
    }


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_markdown(doc: dict) -> str:
    cfg, m = doc["config"], doc["metrics"]
    labels = cfg["platform_labels"]
    out = [
        "# 功能索引（自动生成）",
        "",
        f"> 由 `pm.py build` 从 `features/` + `events/` 折叠生成，请勿手改。",
        f"> 生成时间：{doc['meta']['generated_at']} · 机器：{doc['meta']['generated_by']}",
        "",
        f"## {doc['meta']['iteration']} 迭代",
        "",
        "| 需求 | 优先级 | 版本 | 契约 | " + " | ".join(labels.get(p, p) for p in cfg["platforms"]) + " |",
        "|---|---|---|---|" + "---|" * len(cfg["platforms"]),
    ]
    for f in doc["features"]:
        if f.get("iteration") != doc["meta"]["iteration"]:
            continue
        ct = f.get("contract") or {}
        badge = ct.get("version") or "—"
        if ct.get("version"):
            badge += " ✓" if ct.get("frozen") else " 未冻结"
        cells = " | ".join(GLYPH.get(f["platforms"][p]["status"], "?") for p in cfg["platforms"])
        out.append(f"| {f['id']} {f['title']} | {f.get('priority', '')} | "
                   f"{f.get('revision') or '—'} | {badge} | {cells} |")
    out += ["", "图例：· 未开始 · ◐ 进行中 · ✕ 阻塞 · ◑ 待验收 · ✓ 完成 · — 不涉及", ""]
    if m["risks"]:
        out += ["## 风险", ""]
        out += [f"- **[{r['kind']}]** {r['text']}" for r in m["risks"]]
        out.append("")
    return "\n".join(out)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 迭代面板</title>
<style>
  :root{--bg:#0b1220;--panel:#111a2b;--panel2:#0f1726;--line:#1e293b;--text:#e2e8f0;--muted:#94a3b8}
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:22px 26px 14px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,rgba(56,189,248,.07),transparent)}
  h1{margin:0;font-size:19px;letter-spacing:.3px}
  h1 span{color:var(--muted);font-weight:400;font-size:13px;margin-left:10px}
  .meta{color:var(--muted);font-size:12px;text-align:right}
  main{max-width:1440px;margin:0 auto;padding:18px 26px 48px;display:grid;gap:14px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .kpi .k{color:var(--muted);font-size:12px}
  .kpi .v{font-size:26px;font-weight:650;line-height:1.25;margin-top:2px}
  .kpi .s{color:var(--muted);font-size:11px;margin-top:2px}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
  .panel h2{margin:0 0 12px;font-size:12px;font-weight:600;color:var(--muted);letter-spacing:.8px;text-transform:uppercase}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media (max-width:960px){.cols{grid-template-columns:1fr}}
  .bar-row{display:grid;grid-template-columns:74px 1fr 116px;align-items:center;gap:10px;margin:7px 0}
  .bar-label{font-size:13px}
  .bar-track{height:12px;border-radius:6px;background:#1b2536;overflow:hidden;display:flex}
  .seg{display:block;height:100%}
  .seg-done{background:#34d399}.seg-review{background:#fbbf24}.seg-doing{background:#38bdf8}.seg-blocked{background:#f87171}.seg-todo{background:#334155}
  .bar-num{color:var(--muted);font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
  .scroll{overflow:auto;max-height:560px;border-radius:8px}
  table{border-collapse:collapse;width:100%;font-size:12.5px}
  th,td{padding:6px 8px;border-bottom:1px solid #182234;text-align:left;white-space:nowrap}
  th{position:sticky;top:0;background:var(--panel2);color:var(--muted);font-weight:500;z-index:1}
  td.id{color:var(--muted);font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
  td.title{white-space:normal;min-width:230px}
  th.c,td.st{text-align:center}
  td.st{font-weight:700;width:44px;border-radius:6px}
  .badge{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;background:#1b2536;color:var(--muted)}
  .badge.ok{color:#34d399;background:rgba(52,211,153,.12)}
  .badge.warn{color:#fbbf24;background:rgba(251,191,36,.12)}
  .risk{display:flex;gap:8px;align-items:flex-start;padding:7px 0;border-bottom:1px dashed #182234;font-size:12.5px}
  .risk:last-child{border-bottom:0}
  .risk .dot{width:8px;height:8px;border-radius:99px;margin-top:5px;flex:0 0 auto}
  .lv-high .dot{background:#f87171}.lv-mid .dot{background:#fbbf24}.lv-low .dot{background:#64748b}
  .tag{display:inline-block;padding:0 6px;border-radius:5px;background:#1b2536;color:var(--muted);font-size:11px;margin-right:4px}
  .muted{color:var(--muted)}
  ol,ul{margin:0;padding-left:18px}
  li{margin:4px 0}
  #next li{font-size:12.5px}
  .chart{width:100%;height:auto;display:block}
  .chart .grid{stroke:#182234;stroke-width:1}
  .chart .axis{fill:#64748b;font-size:10px}
  .legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:6px;font-size:11.5px;color:var(--muted)}
  .legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
  footer{max-width:1440px;margin:0 auto;padding:0 26px 30px;color:var(--muted);font-size:11.5px;line-height:1.9}
  .hint{color:#64748b;font-size:11px;margin-top:6px}
  .home{color:#94a3b8;text-decoration:none;font-size:13px;margin-right:8px}
  .home:hover{color:#38bdf8}
  .badge.plan-ok{color:#34d399;background:rgba(52,211,153,.12)}
  .badge.plan-warn{color:#f87171;background:rgba(248,113,113,.12)}
  .board-filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
  .filter{background:#1b2536;color:var(--muted);border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:11.5px;cursor:pointer}
  .filter.on{background:rgba(56,189,248,.15);color:#38bdf8;border-color:#38bdf8}
  .board{display:grid;grid-template-columns:repeat(5,minmax(170px,1fr));gap:10px;overflow:auto;align-items:start}
  @media (max-width:1100px){.board{grid-template-columns:repeat(2,minmax(160px,1fr))}}
  .col{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px;min-height:120px}
  .col h3{margin:0 0 8px;font-size:12px;color:var(--muted);font-weight:600;display:flex;justify-content:space-between}
  .task{background:#16202f;border:1px solid #223049;border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12px}
  .task.dim{opacity:.3}
  .task-title{line-height:1.4;margin-bottom:5px}
  .task-meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:11px}
  .lane-dot{display:inline-block;width:8px;height:8px;border-radius:99px;margin-right:4px}
  .task-deps{margin-top:5px;font-size:10.5px;color:#f87171}
  .task-deps.ok{color:#64748b}
  .ready-badge{color:#34d399;font-size:10.5px;margin-left:4px}
  .hdr-right{display:flex;flex-direction:column;align-items:flex-end;gap:8px}
  .toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
  .toolbar button{background:#16202f;color:#e2e8f0;border:1px solid #223049;border-radius:8px;padding:5px 11px;font-size:12px;cursor:pointer}
  .toolbar button:hover{border-color:#38bdf8;color:#38bdf8}
  .chk{color:#94a3b8;font-size:11px;display:flex;align-items:center;gap:4px}
  body:not(.edit) .edit-only{display:none}
  td.st.clickable,.task.clickable{cursor:pointer}
  td.st.clickable:hover{outline:1px solid #38bdf8}
  .task.clickable:hover{border-color:#38bdf8}
  .modal-backdrop{position:fixed;inset:0;background:rgba(2,6,23,.72);display:flex;align-items:center;justify-content:center;z-index:50;padding:20px}
  .modal-backdrop[hidden]{display:none}
  .modal{background:#111a2b;border:1px solid #223049;border-radius:12px;padding:18px;width:min(580px,100%);max-height:90vh;overflow:auto}
  .modal h3{margin:0 0 12px;font-size:15px}
  .m-row{display:grid;grid-template-columns:92px 1fr;gap:8px;align-items:center;margin:7px 0}
  .m-row label{color:#94a3b8;font-size:12px}
  .m-row input,.m-row select{background:#0f1726;border:1px solid #223049;color:#e2e8f0;border-radius:7px;padding:5px 8px;font-size:12.5px;width:100%}
  .st-btns{display:flex;gap:6px;flex-wrap:wrap}
  .st-btn{background:#16202f;border:1px solid #223049;color:#94a3b8;border-radius:7px;padding:4px 10px;cursor:pointer;font-size:12px}
  .st-btn.on{border-color:#38bdf8;color:#38bdf8;background:rgba(56,189,248,.12)}
  .modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
  .modal-actions button{background:#16202f;border:1px solid #223049;color:#e2e8f0;border-radius:8px;padding:6px 14px;cursor:pointer}
  .modal-actions .primary{background:#38bdf8;color:#06202e;border-color:#38bdf8;font-weight:600}
  .m-sep{border-top:1px solid #1e293b;margin:14px 0;padding-top:12px}
  .m-hint{color:#64748b;font-size:11px}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#16202f;border:1px solid #38bdf8;color:#e2e8f0;padding:8px 16px;border-radius:99px;font-size:12.5px;z-index:60;opacity:0;transition:.2s;pointer-events:none}
  .toast.show{opacity:1}
  .toast.bad{border-color:#f87171;color:#fecaca}
</style>
</head>
<body>
<header>
  <div><h1><a class="home" href="__HOME__">← 项目</a>迭代面板 <span id="iter"></span></h1></div>
  <div class="hdr-right">
    <div class="toolbar edit-only">
      <button id="btn-new">＋ 需求</button>
      <button id="btn-sync">同步</button>
      <label class="chk"><input type="checkbox" id="autosync"> 保存后自动同步</label>
      <span id="dirty" class="muted"></span>
    </div>
    <div class="meta" id="meta"></div>
  </div>
</header>
<main>
  <section class="kpis" id="kpis"></section>
  <section class="panel"><h2>各端进度</h2><div id="bars"></div></section>
  <section class="panel"><h2>需求 × 泳道</h2><div class="scroll"><table id="matrix"></table></div>
    <div class="hint">单元格：· 未开始 · ◐ 进行中 · ✕ 阻塞 · ◑ 待验收 · ✓ 完成 · — 不涉及；悬停看 ETA / 阻塞原因 / revision</div></section>
  <section class="panel"><h2>任务看板 · 方案 → 各泳道实现</h2>
    <div class="board-filters" id="board-filters"></div>
    <div class="board" id="board"></div>
    <div class="hint">卡片 = 需求 × 泳道；上方可筛选泳道 / 负责人；红色「依赖未完成」表示前置任务没做完，「可开工」表示方案已批且依赖满足</div></section>
  <div class="cols">
    <section class="panel"><h2>风险 / 待处理</h2><ul id="risks" style="list-style:none;padding:0"></ul></section>
    <section class="panel"><h2>完成趋势（累计）</h2><div id="chart"></div></section>
  </div>
  <section class="panel"><h2>下一步建议</h2><ol id="next"></ol></section>
</main>
<footer id="foot"></footer>
<div id="modal" class="modal-backdrop" hidden>
  <div class="modal">
    <h3 id="m-title">编辑</h3>
    <div id="m-body"></div>
    <div class="modal-actions">
      <button id="m-cancel">取消</button>
      <button id="m-save" class="primary">保存</button>
    </div>
  </div>
</div>
<div id="toast" class="toast" hidden></div>
<script id="state" type="application/json">__STATE__</script>
<script>
const S = JSON.parse(document.getElementById('state').textContent);
const C = S.config, M = S.metrics, L = C.platform_labels || {};
const ST = {
  todo:{t:'·',c:'#64748b',bg:'transparent'}, doing:{t:'◐',c:'#38bdf8',bg:'rgba(56,189,248,.14)'},
  blocked:{t:'✕',c:'#f87171',bg:'rgba(248,113,113,.16)'}, review:{t:'◑',c:'#fbbf24',bg:'rgba(251,191,36,.16)'},
  done:{t:'✓',c:'#34d399',bg:'rgba(52,211,153,.14)'}, na:{t:'—',c:'#334155',bg:'transparent'}
};
const PC = Object.assign({release:'#f8fafc'}, C.platform_colors || {});
const EDIT = __EDIT__;
const TOKEN = "__TOKEN__";
if (EDIT) document.body.classList.add('edit');
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const pct = (d,t) => t ? Math.round(d*100/t) : 0;
const it = S.meta.iteration_info || {};
document.getElementById('iter').textContent = (it.title || S.meta.iteration) + (it.goal ? ' · ' + it.goal : '');
document.getElementById('meta').innerHTML = '项目 ' + esc(S.meta.project || '') + ' · 生成于 ' + esc(S.meta.generated_at) + ' · by ' + esc(S.meta.generated_by) + '<br>只读视图 · 改动请在各开发机执行 pm.py';

const worst = C.platforms.filter(p => M.per_platform[p].total).sort((a,b)=>M.per_platform[a].pct-M.per_platform[b].pct)[0];
const cards = [
  ['可发布功能', M.releasable.length + '/' + M.total, pct(M.releasable.length, M.total) + '% 全端完成'],
  ['进行中', M.by_status.doing, '含联调 / 待验收'],
  ['阻塞', M.blocked_slots, '需要立刻处理'],
  ['契约未冻结', M.contract_open, '已开工但契约未冻结'],
  ['方案未批', M.plan_open, '已开工但方案未批准'],
  ['版本漂移', M.drift, '实现的 revision 落后'],
  ['最新版采纳', M.adoption_pct + '%', '各端已跟进到最新 revision'],
  ['依赖阻塞', M.dep_blocked, '前置任务未完成'],
  ['待认领', M.queued, '已派工等待 DSH 会话'],
  ['最落后端', worst ? (L[worst] || worst) : '—', worst ? M.per_platform[worst].pct + '% 完成' : '']
];
document.getElementById('kpis').innerHTML = cards.map(c =>
  '<div class="card kpi"><div class="k">' + esc(c[0]) + '</div><div class="v">' + esc(c[1]) + '</div><div class="s">' + esc(c[2]) + '</div></div>').join('');

document.getElementById('bars').innerHTML = C.platforms.map(p => {
  const d = M.per_platform[p];
  const seg = ['done','review','doing','blocked','todo'].map(k => {
    const n = (d.counts && d.counts[k]) || 0;
    return n ? '<span class="seg seg-' + k + '" style="width:' + (n/d.total*100) + '%"></span>' : '';
  }).join('');
  const ad = (M.adoption && M.adoption[p]) ? M.adoption[p] : null;
  const adTxt = ad && ad.pct !== null && ad.pct !== undefined ? ' · 采纳 ' + ad.pct + '%' : '';
  return '<div class="bar-row"><div class="bar-label">' + esc(L[p] || p) + '</div>' +
    '<div class="bar-track">' + seg + '</div>' +
    '<div class="bar-num">' + d.done + '/' + d.total + ' · ' + d.pct + '%' + adTxt + '</div></div>';
}).join('');

const rows = S.features.map(f => {
  const cells = C.platforms.map(p => {
    const cell = f.platforms[p], st = ST[cell.status] || ST.todo;
    const tip = [cell.eta ? 'ETA ' + cell.eta : '', cell.owner ? '@' + cell.owner : '',
                 cell.blocked_by ? '阻塞: ' + cell.blocked_by : '', cell.note || ''].filter(Boolean).join(' · ');
    return '<td class="st" data-f="' + esc(f.id) + '" data-p="' + esc(p) + '" style="color:' + st.c + ';background:' + st.bg + '" title="' + esc(tip) + '">' + st.t + '</td>';
  }).join('');
  const ct = f.contract || {};
  const badge = ct.version
    ? '<span class="badge ' + (ct.frozen ? 'ok' : 'warn') + '">' + esc(ct.version) + (ct.frozen ? ' ✓' : ' 未冻结') + '</span>'
    : '<span class="badge">—</span>';
  const plan = f.plan || {};
  const planBadge = plan.approved
    ? '<span class="badge plan-ok">已批</span>'
    : '<span class="badge plan-warn">未批</span>';
  return '<tr><td class="id">' + esc(f.id) + '</td><td class="title">' + esc(f.title) + '</td>' +
    '<td>' + esc(f.priority || '') + '</td><td class="rev">' + esc(f.revision || '—') + '</td>' +
    '<td>' + planBadge + '</td>' + cells + '<td>' + badge + '</td></tr>';
}).join('');
document.getElementById('matrix').innerHTML =
  '<thead><tr><th>需求</th><th>标题</th><th>P</th><th>R</th><th>方案</th>' +
  C.platforms.map(p => '<th class="c">' + esc(L[p] || p) + '</th>').join('') +
  '<th>契约</th></tr></thead><tbody>' + rows + '</tbody>';

document.getElementById('risks').innerHTML = M.risks.length
  ? M.risks.map(r => '<li class="risk lv-' + esc(r.level) + '"><span class="dot"></span><div><b>' +
      esc(r.kind) + '</b> ' + esc(r.text) +
      (r.feature ? ' <span class="tag">' + esc(r.feature) + '</span>' : '') + '</div></li>').join('')
  : '<li class="muted">暂无风险</li>';

document.getElementById('next').innerHTML = M.next_actions.length
  ? M.next_actions.map(a => '<li><span class="tag">' + esc(L[a.platform] || a.platform) + '</span>' +
      '<b>' + esc(a.id) + '</b> ' + esc(a.title) + ' <span class="muted">' +
      esc(a.status) + (a.eta ? ' · ETA ' + esc(a.eta) : '') + '</span></li>').join('')
  : '<li class="muted">没有待办</li>';

(function board(){
  const T = M.tasks || [];
  const LANES = C.lanes || C.platforms || [];
  const LAB = C.lane_labels || L;
  const PPL = C.people || {};
  const laneName = p => LAB[p] || p;
  const ownerName = o => (PPL[o] && PPL[o].name) || o || '未指派';
  const STATUS = [['todo','待办'],['doing','进行中'],['review','待验收'],['blocked','阻塞'],['done','完成']];
  const filters = [{id:'all', label:'全部'}]
    .concat(LANES.map(p => ({id:'lane:' + p, label:laneName(p)})))
    .concat((M.owners || []).map(o => ({id:'owner:' + o.id, label:ownerName(o.id) + '（' + o.count + '）'})));
  document.getElementById('board-filters').innerHTML = filters.map((f, i) =>
    '<button class="filter' + (i === 0 ? ' on' : '') + '" data-f="' + esc(f.id) + '">' + esc(f.label) + '</button>').join('');
  const card = t => {
    const color = PC[t.lane] || '#94a3b8';
    const deps = t.unmet && t.unmet.length
      ? '<div class="task-deps">依赖未完成：' + t.unmet.map(esc).join(' / ') + '</div>'
      : ((t.depends_on && t.depends_on.length) ? '<div class="task-deps ok">依赖已满足</div>' : '');
    const ready = t.ready ? '<span class="ready-badge">可开工</span>' : '';
    const disp = t.dispatch === 'queued' ? '<span class="badge warn">待认领</span>'
      : (t.session ? '<span class="badge ok">会话</span>' : '');
    return '<div class="task" data-f="' + esc(t.feature) + '" data-p="' + esc(t.lane) + '" data-lane="' + esc(t.lane) + '" data-owner="' + esc(t.owner || '') + '">' +
      '<div class="task-title">' + esc(t.title) + ready + disp + '</div>' +
      '<div class="task-meta"><span><i class="lane-dot" style="background:' + color + '"></i>' + esc(laneName(t.lane)) + '</span>' +
      '<span>· ' + esc(ownerName(t.owner)) + '</span>' +
      (t.rev ? '<span>· ' + esc(t.rev) + '</span>' : '') +
      (t.eta ? '<span>· ETA ' + esc(t.eta) + '</span>' : '') + '</div>' + deps + '</div>';
  };
  document.getElementById('board').innerHTML = STATUS.map(function (s) {
    const list = T.filter(t => t.status === s[0]);
    return '<div class="col"><h3><span>' + s[1] + '</span><span>' + list.length + '</span></h3>' +
      (list.length ? list.map(card).join('') : '<div class="muted" style="font-size:11px">空</div>') + '</div>';
  }).join('');
  document.querySelectorAll('.filter').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.filter').forEach(b => b.classList.toggle('on', b === btn));
      const f = btn.getAttribute('data-f');
      document.querySelectorAll('#board .task').forEach(function (el) {
        let show = f === 'all';
        if (f.indexOf('lane:') === 0) show = el.getAttribute('data-lane') === f.slice(5);
        if (f.indexOf('owner:') === 0) show = el.getAttribute('data-owner') === f.slice(6);
        el.classList.toggle('dim', !show);
      });
    });
  });
})();

(function chart(){
  const tl = M.timeline, days = tl.days || [], series = tl.series || {};
  const el = document.getElementById('chart');
  if (days.length < 2) { el.innerHTML = '<div class="muted">迭代刚开始，趋势数据不足</div>'; return; }
  const W = 640, H = 200, pl = 30, pr = 12, pt = 12, pb = 28;
  const names = Object.keys(series);
  const maxV = Math.max(1, ...names.reduce((a,n) => a.concat(series[n]), []));
  const X = i => pl + i * (W - pl - pr) / (days.length - 1);
  const Y = v => H - pb - v / maxV * (H - pt - pb);
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="chart">';
  for (let g = 0; g <= 4; g++) {
    const v = maxV * g / 4;
    svg += '<line x1="' + pl + '" x2="' + (W-pr) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" class="grid"/>' +
      '<text x="' + (pl-6) + '" y="' + (Y(v)+4) + '" class="axis" text-anchor="end">' + Math.round(v) + '</text>';
  }
  names.forEach(n => {
    const pts = series[n].map((v,i) => X(i).toFixed(1) + ',' + Y(v).toFixed(1)).join(' ');
    svg += '<polyline points="' + pts + '" fill="none" stroke="' + (PC[n] || '#94a3b8') +
      '" stroke-width="2" ' + (n === 'release' ? 'stroke-dasharray="5 4"' : '') + '/>';
  });
  svg += '<text x="' + pl + '" y="' + (H-8) + '" class="axis">' + esc(days[0]) + '</text>' +
    '<text x="' + (W-pr) + '" y="' + (H-8) + '" class="axis" text-anchor="end">' + esc(days[days.length-1]) + '</text></svg>';
  const legend = names.map(n => '<span class="lg"><i style="background:' + (PC[n] || '#94a3b8') + '"></i>' +
    esc(n === 'release' ? '可发布' : (L[n] || n)) + '</span>').join('');
  el.innerHTML = svg + '<div class="legend">' + legend + '</div>';
})();

if (EDIT) {
  const $id = x => document.getElementById(x);
  const post = (p, body) => fetch(p, {method:'POST', headers: Object.assign({'Content-Type':'application/json'}, TOKEN ? {'Authorization':'Bearer ' + TOKEN} : {}), body: JSON.stringify(body)}).then(r => r.json());
  function toast(msg, bad) {
    const t = $id('toast');
    t.textContent = msg;
    t.className = 'toast show' + (bad ? ' bad' : '');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.className = 'toast'; }, 2600);
  }
  function reloadKeepScroll() {
    try { sessionStorage.setItem('pm-scroll', String(window.scrollY)); } catch (e) {}
    location.reload();
  }
  window.addEventListener('load', function () {
    try {
      const y = sessionStorage.getItem('pm-scroll');
      if (y !== null) { window.scrollTo(0, +y); sessionStorage.removeItem('pm-scroll'); }
    } catch (e) {}
  });
  const LANES = C.lanes || C.platforms;
  const LAB = C.lane_labels || L;
  const PPL = C.people || {};
  const laneName = p => LAB[p] || p;
  const ownerName = o => (PPL[o] && PPL[o].name) || o || '未指派';
  const closeModal = () => { $id('modal').hidden = true; };
  $id('m-cancel').onclick = closeModal;
  $id('modal').addEventListener('click', e => { if (e.target === $id('modal')) closeModal(); });

  function openTask(fid, lane) {
    const f = S.features.find(x => x.id === fid);
    if (!f) return;
    const c = f.platforms[lane] || {};
    const revs = (f.revisions || []).map(r => r.rev);
    const plan = f.plan || {};
    const revOpts = ['<option value="">（未记录）</option>'].concat(revs.map(r =>
      '<option value="' + esc(r) + '"' + (c.rev === r ? ' selected' : '') + '>' + esc(r) + '</option>')).join('');
    const disp = c.dispatch || {};
    const sess = c.session;
    const dispText = sess ? ('进行中 @' + esc(String(sess)))
      : (disp.state === 'queued' ? '已派工，等待会话认领' : '未派工');
    const ownerOpts = ['<option value="">（未指派）</option>'].concat(Object.keys(PPL).map(k =>
      '<option value="' + esc(k) + '"' + (c.owner === k ? ' selected' : '') + '>' + esc(PPL[k].name || k) + '</option>')).join('');
    const statusBtns = [['todo','待'],['doing','做'],['blocked','阻'],['review','审'],['done','完'],['na','—']].map(s =>
      '<button type="button" class="st-btn' + (c.status === s[0] ? ' on' : '') + '" data-v="' + s[0] + '">' + s[1] + '</button>').join('');
    $id('m-title').textContent = f.id + ' · ' + f.title;
    $id('m-body').innerHTML =
      '<div class="m-row"><label>泳道</label><div>' + esc(laneName(lane)) + '</div></div>' +
      '<div class="m-row"><label>状态</label><div class="st-btns" id="m-status-btns">' + statusBtns + '</div>' +
      '<input type="hidden" id="m-status" value="' + esc(c.status || 'todo') + '"></div>' +
      '<div class="m-row"><label>负责人</label><select id="m-owner">' + ownerOpts + '</select></div>' +
      '<div class="m-row"><label>已实现版本</label><select id="m-rev">' + revOpts + '</select></div>' +
      '<div class="m-row"><label>ETA</label><input id="m-eta" type="date" value="' + esc(c.eta || '') + '"></div>' +
      '<div class="m-row"><label>备注</label><input id="m-note" value="' + esc(c.note || '') + '" placeholder="一句话说明"></div>' +
      '<div class="m-row"><label>阻塞原因</label><input id="m-blocked" value="' + esc(c.blocked_by || '') + '" placeholder="状态=阻塞时填写"></div>' +
      '<div class="m-row"><label>依赖</label><input id="m-deps" value="' + esc((c.depends_on || []).join(',')) + '" placeholder="F-1:backend,F-2:web"></div>' +
      '<div class="m-sep"></div>' +
      '<div class="m-row"><label>方案</label><div>' + (plan.approved ? '已批准' : '未批准') +
      ' <button type="button" class="st-btn" id="m-plan">' + (plan.approved ? '撤销批准' : '批准方案') + '</button></div></div>' +
      '<div class="m-row"><label>记录变更</label><div style="display:flex;gap:6px;align-items:center">' +
      '<input id="m-newrev" placeholder="r' + (revs.length + 1) + '" style="width:70px">' +
      '<label class="chk"><input type="checkbox" id="m-breaking"> 破坏性</label>' +
      '<button type="button" class="st-btn" id="m-change">记录</button></div></div>' +
      '<div class="m-sep"></div>' +
      '<div class="m-row"><label>DSH 会话</label><div>' + dispText +
      ' <button type="button" class="st-btn" id="m-dispatch">派给 DSH 会话</button></div></div>' +
      '<div class="m-hint">派工后，在 DSH 会话里说「领任务」，或运行 pm next / pm task ' + esc(fid + ':' + lane) + '</div>';
    $id('modal').hidden = false;
    $id('m-status-btns').addEventListener('click', e => {
      const b = e.target.closest('.st-btn');
      if (!b) return;
      $id('m-status').value = b.dataset.v;
      $id('m-status-btns').querySelectorAll('.st-btn').forEach(x => x.classList.toggle('on', x === b));
    });
    $id('m-plan').onclick = async () => {
      const r = await post('/api/plan', {feature: fid, approved: !plan.approved, doc: plan.doc || null});
      if (r.ok) reloadKeepScroll(); else toast(r.error || '操作失败', true);
    };
    $id('m-change').onclick = async () => {
      const rev = ($id('m-newrev').value || '').trim();
      if (!/^r\d+$/.test(rev)) { toast('版本号形如 r2', true); return; }
      const r = await post('/api/change', {feature: fid, rev: rev, breaking: $id('m-breaking').checked, note: $id('m-note').value || null});
      if (r.ok) reloadKeepScroll(); else toast(r.error || '操作失败', true);
    };
    $id('m-dispatch').onclick = async () => {
      const r = await post('/api/dispatch', {feature: fid, platform: lane, note: $id('m-note').value || null});
      if (!r.ok) { toast(r.error || '派工失败', true); return; }
      try { await navigator.clipboard.writeText(r.prompt || ''); toast('已派工，提示词已复制'); }
      catch (e) { toast('已派工（提示词见 pm task ' + fid + ':' + lane + '）'); }
      reloadKeepScroll();
    };
    $id('m-save').onclick = async () => {
      const deps = ($id('m-deps').value || '').split(',').map(s => s.trim()).filter(Boolean);
      const payload = {
        feature: fid, platform: lane, status: $id('m-status').value,
        owner: $id('m-owner').value || null, rev: $id('m-rev').value || null,
        eta: $id('m-eta').value || null, note: $id('m-note').value || null,
        blocked_by: $id('m-blocked').value || null, depends_on: deps
      };
      const r = await post('/api/task', payload);
      if (!r.ok) { toast(r.error || '保存失败', true); return; }
      if ($id('autosync') && $id('autosync').checked) {
        const s = await post('/api/sync', {});
        toast(s.ok ? '已保存并同步' : '已保存，同步失败', !s.ok);
      }
      reloadKeepScroll();
    };
  }

  function openNew() {
    const iters = (C.iterations || []).filter(i => i.id !== 'backlog');
    const iterOpts = iters.map(i => '<option value="' + esc(i.id) + '">' + esc(i.title || i.id) + '</option>').join('');
    $id('m-title').textContent = '新建需求';
    $id('m-body').innerHTML =
      '<div class="m-row"><label>ID</label><input id="n-id" placeholder="F-2026-11-15"></div>' +
      '<div class="m-row"><label>标题</label><input id="n-title" placeholder="一句话描述"></div>' +
      '<div class="m-row"><label>优先级</label><select id="n-priority"><option>P0</option><option selected>P1</option><option>P2</option></select></div>' +
      '<div class="m-row"><label>迭代</label><select id="n-iter">' + iterOpts + '</select></div>' +
      '<div class="m-hint">默认全部泳道参与；创建后在矩阵里调整</div>';
    $id('modal').hidden = false;
    $id('m-save').onclick = async () => {
      const r = await post('/api/feature', {
        id: $id('n-id').value.trim(), title: $id('n-title').value.trim(),
        priority: $id('n-priority').value, iteration: $id('n-iter').value
      });
      if (r.ok) reloadKeepScroll(); else toast(r.error || '创建失败', true);
    };
  }

  $id('btn-new').onclick = openNew;
  $id('btn-sync').onclick = async () => {
    $id('btn-sync').disabled = true;
    const r = await post('/api/sync', {});
    $id('btn-sync').disabled = false;
    toast(r.ok ? (r.message || '已同步') : ('同步失败：' + (r.error || '')), !r.ok);
    loadMeta();
  };
  try { $id('autosync').checked = localStorage.getItem('pm-autosync') === '1'; } catch (e) {}
  $id('autosync').onchange = function () {
    try { localStorage.setItem('pm-autosync', this.checked ? '1' : '0'); } catch (e) {}
  };
  function loadMeta() {
    fetch('/api/meta', {headers: TOKEN ? {'Authorization':'Bearer ' + TOKEN} : {}}).then(r => r.json()).then(m => {
      $id('dirty').textContent = (m.dirty ? '未同步 ' + m.dirty + ' 项' : '已同步') + (m.machine ? ' · ' + m.machine : '');
      $id('dirty').style.color = m.dirty ? '#fbbf24' : '#34d399';
    }).catch(() => {});
  }
  loadMeta();
  document.querySelectorAll('#matrix td.st').forEach(td => {
    td.classList.add('clickable');
    td.addEventListener('click', () => openTask(td.getAttribute('data-f'), td.getAttribute('data-p')));
  });
  document.getElementById('board').addEventListener('click', e => {
    const card = e.target.closest('.task');
    if (card) openTask(card.getAttribute('data-f'), card.getAttribute('data-p'));
  });
}

const seen = Object.keys(S.machines_last_seen || {}).map(m =>
  esc((C.machines && C.machines[m] && C.machines[m].label) || m) + '：' + esc(S.machines_last_seen[m] || '—')).join(' · ');
document.getElementById('foot').innerHTML = '机器最近事件：' + (seen || '—') +
  '<br>仓库：' + Object.keys(C.repos || {}).map(k => '<span class="tag">' + esc(C.repos[k].name) + '</span>' + esc(C.repos[k].role)).join(' ');
</script>
</body>
</html>
"""


def esc_html(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_dashboard(doc: dict, editable: bool = False, home: str = "../index.html",
                     token: str = "") -> str:
    payload = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__TITLE__", str(doc["meta"]["name"]))
    html = html.replace("__EDIT__", "true" if editable else "false")
    html = html.replace("__HOME__", home)
    html = html.replace("__PROJECT__", str(doc["meta"].get("project") or ""))
    html = html.replace("__TOKEN__", token or "")
    return html.replace("__STATE__", payload)


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------
def build_one(cfg: dict, quiet: bool = False) -> dict:
    ensure_layout()
    gen_tokens(quiet=True)
    gen_screens(quiet=True)
    feats = load_features()
    events = read_events()
    state = fold(cfg, feats, events)
    metrics = compute(cfg, state, events)
    itinfo = next((i for i in cfg["iterations"] if i["id"] == cfg["current_iteration"]), {})
    ordered = sorted(
        state.values(),
        key=lambda s: (
            s["feature"].get("iteration") != cfg["current_iteration"],
            PRIORITY_ORDER.get(s["feature"].get("priority"), 9),
            s["feature"]["id"],
        ),
    )
    features = []
    for s in ordered:
        f = dict(s["feature"])
        f["platforms"] = s["platforms"]
        f["contract"] = s["contract"]
        f["notes"] = s["notes"]
        f["last_event"] = s["last_event"]
        features.append(f)
    last_seen = {}
    for ev in events:
        m, ts = ev.get("machine"), ev.get("ts")
        if m and ts and (m not in last_seen or ts > last_seen[m]):
            last_seen[m] = ts
    doc = {
        "meta": {
            "name": cfg["name"],
            "project": CURRENT_PID,
            "hub_name": cfg.get("hub_name"),
            "generated_at": now_utc(),
            "generated_by": machine_name(cfg),
            "iteration": cfg["current_iteration"],
            "iteration_info": itinfo,
        },
        "config": {
            "platforms": cfg["platforms"],
            "platform_labels": cfg["platform_labels"],
            "platform_colors": palette(cfg),
            "lanes": cfg["lanes"],
            "lane_labels": cfg["lane_labels"],
            "lane_colors": palette(cfg),
            "lane_order": cfg.get("lane_order") or cfg["platforms"],
            "people": cfg.get("people") or {},
            "shots_dir": cfg.get("shots_dir", "shots"),
            "machines": cfg.get("machines") or {},
            "repos": cfg.get("repos") or {},
            "iterations": cfg["iterations"],
        },
        "features": features,
        "metrics": metrics,
        "machines_last_seen": last_seen,
    }
    out = out_dir()
    (out / "state.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "dashboard.html").write_text(render_dashboard(doc), encoding="utf-8")
    (out / "FEATURES.md").write_text(render_markdown(doc), encoding="utf-8")
    if not quiet:
        print_summary(doc)
    return doc


def build_all(quiet: bool = False) -> dict:
    hub = load_hub()
    env = os.environ.get("PM_PROJECT", "").strip()
    pids = [env] if env else list(hub["projects"])
    docs = {}
    for pid in pids:
        use_project(pid)
        docs[pid] = build_one(load_cfg(), quiet)
    write_index(docs, hub)
    if not quiet and len(docs) > 1:
        print_index_summary(docs, hub)
    return docs


def build(cfg=None, quiet: bool = False) -> dict:
    return build_all(quiet)


def build_active(quiet: bool = True) -> dict:
    return build_one(load_cfg(), quiet)


def render_index(docs: dict, hub: dict, editable: bool = False, serve: bool = False,
                 token: str = "") -> str:
    cards = []
    for pid, doc in docs.items():
        m, cfg = doc["metrics"], doc["config"]
        total = m["total"] or 0
        pct = round(100 * len(m["releasable"]) / total) if total else 0
        lanes = "".join(
            f'<span class="pill" style="border-color:{cfg["lane_colors"].get(p, "#334155")}">'
            f'{esc_html(cfg["lane_labels"].get(p, p))} '
            f'{m["per_platform"][p]["done"]}/{m["per_platform"][p]["total"]}</span>'
            for p in cfg["platforms"]
        )
        href = f"/p/{pid}" if serve else f"./{pid}/dashboard.html"
        cards.append(
            f'<a class="proj" href="{href}">'
            f'<div class="pname">{esc_html(hub["projects"][pid]["name"])}</div>'
            f'<div class="pbig">{len(m["releasable"])}/{total}'
            f'<span> 可发布 · {pct}%</span></div>'
            f'<div class="psub">阻塞 {m["blocked_slots"]} · 待认领 {m["queued"]} · '
            f'漂移 {m["drift"]} · 风险 {len(m["risks"])}</div>'
            f'<div class="planebar">{lanes}</div></a>'
        )
    sync = ('<button id="sync">同步</button><span id="msg" class="msg"></span>'
            if editable else "")
    js = """
<script>
const TOKEN = "__TOKEN__";
const b = document.getElementById('sync');
if (b) b.addEventListener('click', async () => {
  b.disabled = true;
  const r = await fetch('/api/sync', {method:'POST', headers: Object.assign({'Content-Type':'application/json'}, TOKEN ? {'Authorization':'Bearer ' + TOKEN} : {}), body:'{}'});
  const j = await r.json();
  document.getElementById('msg').textContent = j.ok ? (j.message || '已同步') : ('失败：' + (j.error || ''));
  b.disabled = false;
});
</script>""" if editable else ""
    js = js.replace("__TOKEN__", token or "")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc_html(hub["name"])} · 项目总览</title>
<style>
:root{{--bg:#0b1220;--panel:#111a2b;--line:#1e293b;--text:#e2e8f0;--muted:#94a3b8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:14px/1.55 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
padding:22px 26px 14px;border-bottom:1px solid var(--line)}}
h1{{margin:0;font-size:19px}}h1 span{{color:var(--muted);font-size:13px;margin-left:10px;font-weight:400}}
.toolbar{{display:flex;gap:10px;align-items:center}}
button{{background:#16202f;color:#e2e8f0;border:1px solid #223049;border-radius:8px;
padding:6px 14px;font-size:12.5px;cursor:pointer}}button:hover{{border-color:#38bdf8;color:#38bdf8}}
.msg{{color:var(--muted);font-size:12px}}
main{{max-width:1200px;margin:0 auto;padding:20px 26px 48px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
.proj{{display:block;background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;text-decoration:none;color:inherit}}
.proj:hover{{border-color:#38bdf8}}
.pname{{color:var(--muted);font-size:12.5px}}
.pbig{{font-size:28px;font-weight:650;margin:4px 0 2px}}
.pbig span{{font-size:12px;font-weight:400;color:var(--muted)}}
.psub{{color:var(--muted);font-size:12px}}
.planebar{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}
.pill{{border:1px solid #334155;border-radius:99px;padding:1px 8px;font-size:11px;color:var(--muted)}}
.hint{{color:#64748b;font-size:11.5px;margin-top:18px}}
</style></head><body>
<header><h1>项目总览 <span>{esc_html(hub["name"])}</span></h1><div class="toolbar">{sync}</div></header>
<main><div class="grid">{''.join(cards)}</div>
<div class="hint">点项目进入看板；本地面板可写，云端面板只读。</div></main>{js}</body></html>"""


def write_index(docs: dict, hub: dict):
    out = HUB_ROOT / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_index(docs, hub), encoding="utf-8")


def print_index_summary(docs: dict, hub: dict):
    print(f"\n=== {hub['name']} · 项目总览 ===")
    print(f"{pad('项目', 18)}{pad('可发布', 10)}{pad('阻塞', 6)}{pad('待认领', 8)}"
          f"{pad('漂移', 6)}{pad('风险', 6)}")
    for pid, doc in docs.items():
        m = doc["metrics"]
        rel = f"{len(m['releasable'])}/{m['total']}"
        print(f"{pad(pid, 18)}{pad(rel, 10)}{pad(m['blocked_slots'], 6)}"
              f"{pad(m['queued'], 8)}{pad(m['drift'], 6)}{pad(len(m['risks']), 6)}")
    print(f"\n总览: {HUB_ROOT / 'out' / 'index.html'}")


def bar(pctv: int, width: int = 18) -> str:
    n = round(pctv / 100 * width)
    return "#" * n + "." * (width - n)


def print_summary(doc: dict):
    m, cfg = doc["metrics"], doc["config"]
    labels = cfg["platform_labels"]
    print(f"\n=== {doc['meta']['name']} · {doc['meta']['iteration']} 迭代 ===")
    print(f"生成 {doc['meta']['generated_at']} by {doc['meta']['generated_by']}")
    print(
        f"可发布 {len(m['releasable'])}/{m['total']} · 阻塞 {m['blocked_slots']} · "
        f"契约未冻结 {m['contract_open']} · 方案未批 {m['plan_open']} · "
        f"版本漂移 {m['drift']} · 采纳 {m['adoption_pct']}% · 需求池 {m['backlog']}"
    )
    print()
    for p in cfg["platforms"]:
        d = m["per_platform"][p]
        print(f"  {pad(labels.get(p, p), 8)}{bar(d['pct'])} {d['done']:>2}/{d['total']:<2} {d['pct']:>3}%")
    if m["risks"]:
        print("\n风险:")
        for r in m["risks"][:6]:
            print(f"  [{r['level']}] {r['text']}")
    if m["next_actions"]:
        print("\n下一步:")
        for a in m["next_actions"][:5]:
            print(f"  {pad(a['id'], 16)}{pad(labels.get(a['platform'], a['platform']), 8)}"
                  f"{pad(a['status'], 9)}{a['title']}")
    print(f"\n面板: {out_dir() / 'dashboard.html'}")


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------
def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(HUB_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 通用化：预设 / 脚手架 / 令牌与屏幕规格生成 / 检查
# --------------------------------------------------------------------------
PRESETS = {
    "multi": {
        "desc": "后端 + Web + iOS + Android + 鸿蒙",
        "platforms": [("backend", "后端"), ("web", "Web"), ("ios", "iOS"),
                      ("android", "Android"), ("hos", "鸿蒙")],
    },
    "mobile": {
        "desc": "后端 + iOS + Android",
        "platforms": [("backend", "后端"), ("ios", "iOS"), ("android", "Android")],
    },
    "web": {"desc": "后端 + Web", "platforms": [("backend", "后端"), ("web", "Web")]},
    "api": {"desc": "只有后端", "platforms": [("backend", "后端")]},
}

DEFAULT_MACHINES = [
    ("windows-home", "家用 Windows（日常）", True),
    ("mac-weekend", "Mac（周末）", True),
    ("cloud", "云服务器（只读聚合）", False),
]

DEFAULT_TOKENS = {
    "version": "1.0",
    "color": {
        "bg": "#0B1220",
        "surface": "#111A2B",
        "text": "#E2E8F0",
        "muted": "#94A3B8",
        "primary": "#38BDF8",
        "success": "#34D399",
        "warning": "#FBBF24",
        "danger": "#F87171",
    },
    "space": {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32},
    "radius": {"sm": 6, "md": 12, "full": 999},
    "font": {"caption": 12, "body": 14, "title": 20},
    "motion": {"fast": 120, "normal": 200},
}

HOOK_SH = """#!/bin/sh
# pm setup 安装：pull 之后自动刷新面板
if command -v python3 >/dev/null 2>&1; then
  python3 pm.py build >/dev/null 2>&1 || true
elif command -v python >/dev/null 2>&1; then
  python pm.py build >/dev/null 2>&1 || true
fi
"""

README_TMPL = """# {name} · roadmap

> 这是 **roadmap 数据仓**（需求/事件/面板数据），由 `pm.py scaffold` 生成。
> 用 pm 工具仓的 `pm.py --roadmap <本目录>` 操作它；本仓库不含 pm.py（除非 scaffold --vendor）。

## 每天

双击 `start-pm.cmd`（Windows）/ `start-pm.command`（Mac）/ `./start-pm.sh`（Linux）打开面板：
派工、看板、同步。

DSH 会话：`pm bind <project>` 绑定项目，`pm next` 领任务，`pm report --status done --rev rN` 上报。

## 首次（每台机器各一次）

```bash
python pm.py setup --machine <本机名>  # 写 .machine + 装 post-merge hook
python pm.py new --id F-1 --title "第一个需求" --priority P0
```

## 云端只读聚合

```cron
*/5 * * * * cd <repo> && git pull -q --rebase && python3 pm.py build && rsync -a out/ /srv/www/hub/
```
"""


def parse_platforms(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        pid, _, label = part.partition(":")
        pid = pid.strip()
        out.append((pid, label.strip() or pid))
    return out


def parse_machines(spec: str):
    out = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = [x.strip() for x in part.split(":")]
        mid = bits[0]
        label = bits[1] if len(bits) > 1 and bits[1] else mid
        writable = not (len(bits) > 2 and bits[2] in ("ro", "readonly", "0", "false"))
        out[mid] = {"label": label, "writable": writable}
    return out


def parse_repos(spec: str):
    out = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = [x.strip() for x in part.split(":")]
        rid = bits[0]
        out[rid] = {
            "name": bits[1] if len(bits) > 1 and bits[1] else rid,
            "role": bits[2] if len(bits) > 2 else "",
        }
    return out


def parse_people(spec: str):
    out = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        pid, _, name = part.partition(":")
        pid = pid.strip()
        out[pid] = {"name": name.strip() or pid}
    return out


def parse_iteration(spec):
    """格式：2026-11:2026-11-02:2026-11-29:目标"""
    today = date.today()
    if not spec:
        it = f"{today.year}-{today.month:02d}"
        return {"id": it, "title": f"{it} 迭代", "start": None, "end": None, "goal": ""}
    bits = [x.strip() for x in spec.split(":")]
    return {
        "id": bits[0],
        "title": f"{bits[0]} 迭代",
        "start": bits[1] if len(bits) > 1 and bits[1] else None,
        "end": bits[2] if len(bits) > 2 and bits[2] else None,
        "goal": bits[3] if len(bits) > 3 else "",
    }


def make_cfg(name, platforms, machines, repos, iteration, people=None):
    plats = [p for p, _ in platforms]
    it = parse_iteration(iteration)
    return {
        "name": name,
        "current_iteration": it["id"],
        "iterations": [it, {"id": "backlog", "title": "需求池", "start": None,
                            "end": None, "goal": ""}],
        "lanes": plats,
        "lane_labels": dict(platforms),
        "lane_short": {},
        "lane_colors": {},
        "lane_order": plats,
        "people": people or {"me": {"name": "我"}},
        "mobile_platforms": [p for p in plats if p not in ("backend", "web")],
        "machines": machines,
        "machine_aliases": {},
        "risk_lag_days": 7,
        "stale_days": 5,
        "repos": repos,
        "tokens_path": "design/tokens.json",
        "screens_dir": "spec/screens",
        "shots_dir": "shots",
    }


DSH_TMPL = """# 在 DSH 会话里操作

绑定项目：`pm bind <project>`（一次；`pm projects` 查看）
领任务：`pm next` 或 `pm task <ID>:<lane>`
上报：`pm report --status review|done --rev rN --note "..."`
需求：`pm new` / `pm change` / `pm plan`
收尾：`pm check && pm sync`

不要手改 `events/` 下的文件；所有状态变更都走命令。
"""


def write_launchers(target: Path):
    tool = Path(__file__).resolve()
    win = target / "start-pm.cmd"
    win.write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        f'python "{tool}" serve --roadmap "%~dp0" --open\r\n'
        "if errorlevel 1 pause\r\n",
        encoding="utf-8", newline="")
    for name in ("start-pm.command", "start-pm.sh"):
        f = target / name
        f.write_text(
            "#!/bin/sh\n"
            "cd \"$(dirname \"$0\")\"\n"
            f'python3 "{tool}" serve --roadmap "$(dirname "$0")" --open\n',
            encoding="utf-8")
        try:
            os.chmod(f, 0o755)
        except OSError:
            pass


def write_hub_files(target: Path, cfg: dict):
    """写项目目录：features/events/spec/design/shots + 项目 hub.json + tokens。"""
    for d in ("features", "events", "spec/screens", "design", "shots"):
        (target / d).mkdir(parents=True, exist_ok=True)
        keep = target / d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    (target / "hub.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tokens = target / "design" / "tokens.json"
    if not tokens.exists():
        tokens.write_text(json.dumps(DEFAULT_TOKENS, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def write_data_files(target: Path, hub: dict):
    """只写 roadmap 数据仓需要的文件：hub.json + .gitignore/.gitattributes。"""
    (target / ".gitignore").write_text(
        "out/\n.machine\n.pm/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    (target / ".gitattributes").write_text(
        "* text=auto\n*.jsonl text eol=lf\n*.json  text eol=lf\n*.py    text eol=lf\n*.md    text eol=lf\n",
        encoding="utf-8")
    (target / "hub.json").write_text(
        json.dumps(hub, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_root_files(target: Path, hub: dict, vendor: bool = False):
    """写完整模板仓：数据文件 + README/DSH/启动器/pm.py（仅 scaffold 使用）。"""
    write_data_files(target, hub)
    (target / "README.md").write_text(README_TMPL.format(name=hub["name"]), encoding="utf-8")
    dsh = target / "DSH.md"
    if not dsh.exists():
        dsh.write_text(DSH_TMPL, encoding="utf-8")
    write_launchers(target)
    if vendor:
        src = Path(__file__).resolve()
        dst = (target / "pm.py").resolve()
        if src != dst:
            shutil.copy2(src, dst)


def _camel(group: str, key: str) -> str:
    parts = re.split(r"[-_.\s]+", f"{group} {key}")
    return parts[0].lower() + "".join(x.capitalize() for x in parts[1:] if x)


def gen_tokens(quiet=False) -> bool:
    cfg = load_cfg()
    src = ROOT / cfg.get("tokens_path", "design/tokens.json")
    if not src.exists():
        if not quiet:
            warn(f"没有 {src.name}，跳过令牌生成")
        return False
    try:
        t = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{src.name} 不是合法 JSON: {e}")
    out = out_dir() / "generated"
    out.mkdir(parents=True, exist_ok=True)
    groups = {k: v for k, v in t.items() if isinstance(v, dict) and not k.startswith("$")}
    units = {"space": "px", "radius": "px", "font": "px", "motion": "ms"}
    css = [":root {"]
    md = ["# 设计令牌（自动生成）", "",
          f"> 由 `pm gen` 从 `{src.name}` 生成，勿手改。", "",
          "| 令牌 | 值 |", "|---|---|"]
    for g, vals in groups.items():
        for k, v in vals.items():
            unit = units.get(g, "")
            is_num = isinstance(v, (int, float)) and not isinstance(v, bool)
            css_key = re.sub(r"([a-z])([A-Z])", r"\1-\2", k).lower()
            css.append(f"  --{g}-{css_key}: {v}{unit};" if is_num and unit
                       else f"  --{g}-{css_key}: {v};")
            md.append(f"| `{g}.{k}` | `{v}` |")
    css.append("}")
    (out / "tokens.css").write_text("\n".join(css) + "\n", encoding="utf-8")
    (out / "tokens.json").write_text(json.dumps(t, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    (out / "tokens.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    sw = ["// 由 pm gen 从 design/tokens.json 生成，勿手改", "enum DesignTokens {"]
    kt = ["// 由 pm gen 从 design/tokens.json 生成，勿手改", "object DesignTokens {"]
    ets = ["// 由 pm gen 从 design/tokens.json 生成，勿手改", "export class DesignTokens {"]
    for g, vals in groups.items():
        sw.append(f"    // {g}")
        kt.append(f"    // {g}")
        ets.append(f"  // {g}")
        for k, v in vals.items():
            n = _camel(g, k)
            if isinstance(v, str) and re.match(r"^#[0-9A-Fa-f]{6}$", v):
                sw.append(f"    static let {n}: UInt32 = 0x{v[1:].upper()}")
                kt.append(f"    const val {n}: Long = 0xFF{v[1:].upper()}L")
                ets.append(f"  static readonly {n}: string = '{v}'")
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                sw.append(f"    static let {n}: Double = {v}")
                kt.append(f"    const val {n}: Float = {v}f")
                ets.append(f"  static readonly {n}: number = {v}")
            else:
                sw.append(f'    static let {n}: String = "{v}"')
                kt.append(f'    const val {n}: String = "{v}"')
                ets.append(f"  static readonly {n}: string = '{v}'")
    sw.append("}")
    kt.append("}")
    ets.append("}")
    (out / "Tokens.swift").write_text("\n".join(sw) + "\n", encoding="utf-8")
    (out / "Tokens.kt").write_text("\n".join(kt) + "\n", encoding="utf-8")
    (out / "Tokens.ets").write_text("\n".join(ets) + "\n", encoding="utf-8")
    if not quiet:
        print(f"令牌: {out / 'tokens.css'} 等 6 个文件")
    return True


def gen_screens(quiet=False) -> bool:
    cfg = load_cfg()
    d = ROOT / cfg.get("screens_dir", "spec/screens")
    if not d.exists() or not list(d.glob("*.json")):
        if not quiet:
            warn(f"{d.name}/ 下没有屏幕规格，跳过")
        return False
    feats = load_features()
    out = out_dir() / "screens"
    out.mkdir(parents=True, exist_ok=True)
    index = ["# 屏幕规格索引（自动生成）", "",
             "> 由 `pm gen` 从 `spec/screens/*.json` 生成。每份规格可直接作为 AI 实现的上下文。", "",
             "| 屏幕 | 需求 | 参考端 | 状态数 | 断言数 | 差异白名单 |", "|---|---|---|---|---|---|"]
    n = 0
    for p in sorted(d.glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            warn(f"{p.name} 解析失败: {e}")
            continue
        sid = s.get("screen") or p.stem
        states = s.get("states") or {}
        devs = s.get("deviations") or {}
        asrt = s.get("assertions") or []
        ref = (s.get("reference") or {}).get("platform", "—")
        md = [f"# 屏幕规格：{s.get('title', sid)}（{sid}）", "",
              f"> 由 `pm gen` 从 `{cfg.get('screens_dir', 'spec/screens')}/{p.name}` 生成，勿手改。",
              f"> 需求：{s.get('feature', '—')} · 参考实现：{ref}", ""]
        anatomy = s.get("anatomy") or []
        if anatomy:
            md += ["## 结构（从上到下）", ""] + [f"{i + 1}. {x}" for i, x in enumerate(anatomy)] + [""]
        if states:
            md += ["## 状态与文案", "", "| 状态 | 文案 | 语义断言 |", "|---|---|---|"]
            for st, v in states.items():
                copy = " / ".join(v.get("copy") or []) or "—"
                md.append(f"| {st} | {copy} | {v.get('assert') or '—'} |")
            md.append("")
        copy = s.get("copy") or {}
        if copy:
            md += ["## 文案表（禁止自创文案）", "", "| key | 文案 |", "|---|---|"]
            md += [f"| `{k}` | {v} |" for k, v in copy.items()]
            md.append("")
        if devs:
            md += ["## 平台差异白名单", ""] + [f"- **{k}**：{v}" for k, v in devs.items()] + [""]
        if asrt:
            md += ["## 验收断言", ""] + [f"- [ ] {x}" for x in asrt] + [""]
        (out / f"{sid}.md").write_text("\n".join(md), encoding="utf-8")
        index.append(f"| {sid} | {s.get('feature', '—')} | {ref} | {len(states)} | "
                     f"{len(asrt)} | {', '.join(devs) or '—'} |")
        n += 1
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    if not quiet:
        print(f"屏幕规格: {n} 份 → {out}")
    return True


def init_roadmap(name=None, quiet=False) -> dict:
    """在 HUB_ROOT 创建最小 roadmap（hub.json + projects/<pid>/）。"""
    HUB_ROOT.mkdir(parents=True, exist_ok=True)
    preset = PRESETS["multi"]
    name = name or HUB_ROOT.name
    pid = sanitize(name)
    platforms = list(preset["platforms"])
    pcfg = make_cfg(name, platforms, {}, {}, None)
    hub = {
        "name": name,
        "machines": {m: {"label": l, "writable": w} for m, l, w in DEFAULT_MACHINES},
        "people": {"me": {"name": "我"}},
        "machine_aliases": {},
        "projects": {pid: {"name": name, "path": f"projects/{pid}"}},
        "default_project": pid,
    }
    write_data_files(HUB_ROOT, hub)
    write_hub_files(HUB_ROOT / "projects" / pid, pcfg)
    use_project(pid)
    if not quiet:
        print(f"已初始化：hub.json + projects/{pid}/")
    return hub


def ensure_hub() -> dict:
    """已有 hub.json 就加载；空目录/仅 .git 时自动初始化；非空目录拒绝。"""
    if (HUB_ROOT / "hub.json").exists():
        return load_hub()
    if HUB_ROOT.exists():
        extra = [x for x in HUB_ROOT.iterdir() if x.name != ".git"]
        if extra:
            die(f"缺少 hub.json（{HUB_ROOT}），目录非空，拒绝自动初始化；"
                f"请用空目录，或先运行 pm.py --roadmap {HUB_ROOT} init")
    print(f"roadmap 为空，自动初始化: {HUB_ROOT}")
    return init_roadmap(quiet=True)


def _new_hub() -> dict:
    return {
        "name": HUB_ROOT.name or "workspace",
        "machines": {m: {"label": l, "writable": w} for m, l, w in DEFAULT_MACHINES},
        "people": {"me": {"name": "我"}},
        "machine_aliases": {},
        "projects": {},
        "default_project": None,
    }


def project_add(a):
    HUB_ROOT.mkdir(parents=True, exist_ok=True)
    fresh = not (HUB_ROOT / "hub.json").exists()
    hub = _new_hub() if fresh else load_hub()
    pid = sanitize(a.id)
    if pid in hub["projects"]:
        die(f"项目已存在: {pid}")
    dst = HUB_ROOT / "projects" / pid
    if dst.exists():
        die(f"目录已存在: {dst}（先移走或换一个 --id）")
    name = a.name or pid
    if a.from_dir:
        src_dir = Path(a.from_dir).expanduser().resolve()
        if not src_dir.is_dir():
            die(f"不是目录: {src_dir}")
        shutil.copytree(src_dir, dst)
        if not (dst / "hub.json").exists():
            pcfg = make_cfg(name, list(PRESETS["multi"]["platforms"]),
                            hub.get("machines", {}), {}, a.iteration)
            write_hub_files(dst, pcfg)
    else:
        lanes = parse_platforms(a.lanes) if a.lanes else list(PRESETS["multi"]["platforms"])
        repos = parse_repos(a.repos) if a.repos else {}
        pcfg = make_cfg(name, lanes, hub.get("machines", {}), repos, a.iteration)
        write_hub_files(dst, pcfg)
    hub["projects"][pid] = {"name": name, "path": f"projects/{pid}"}
    if a.default or not hub.get("default_project"):
        hub["default_project"] = pid
    (HUB_ROOT / "hub.json").write_text(
        json.dumps(hub, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if fresh:
        write_data_files(HUB_ROOT, hub)
    use_project(pid)
    print(f"已添加项目: {pid}（{name}）")
    print(f"  泳道: {' / '.join(load_cfg()['platforms'])}")
    print(f"  默认项目: {hub['default_project']}")
    if a.from_dir:
        print(f"  已导入: {a.from_dir}")


def cmd_project(a):
    if a.action == "list":
        hub = load_hub()
        for pid, meta in hub["projects"].items():
            mark = " *" if pid == hub.get("default_project") else ""
            print(f"{pid:<20}{meta.get('name', pid)}{mark}")
        return
    project_add(a)


def cmd_init(a):
    if (HUB_ROOT / "hub.json").exists():
        print("hub.json 已存在，跳过（要重建请先删除）")
        return
    init_roadmap(a.name)
    pid = sanitize(a.name or HUB_ROOT.name)
    print("下一步：")
    print("  python pm.py setup --machine windows-home   # 每台机器执行一次")
    print("  python pm.py bind " + pid + "              # DSH 会话绑定项目")
    print("  python pm.py new --id F-1 --title '第一个需求'")


def cmd_scaffold(a):
    if a.list_presets:
        print("可用预设：")
        for k, v in PRESETS.items():
            print(f"  {pad(k, 8)}{v['desc']}")
        return
    target = Path(a.dir).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        die(f"目录非空: {target}（换一个 --dir，或先清空）")
    target.mkdir(parents=True, exist_ok=True)
    preset = PRESETS.get(a.preset or "multi")
    if preset is None:
        die(f"未知预设 {a.preset}（可用: {', '.join(PRESETS)}）")
    platforms = parse_platforms(a.platforms) if a.platforms else list(preset["platforms"])
    machines = (parse_machines(a.machines) if a.machines
                else {m: {"label": l, "writable": w} for m, l, w in DEFAULT_MACHINES})
    repos = parse_repos(a.repos) if a.repos else {}
    people = parse_people(a.people) if a.people else {"me": {"name": "我"}}
    name = a.name or target.name
    pid = sanitize(a.id or name)
    pcfg = make_cfg(name, platforms, machines, repos, a.iteration, people)
    hub = {
        "name": name,
        "machines": machines,
        "people": people,
        "machine_aliases": {},
        "projects": {pid: {"name": name, "path": f"projects/{pid}"}},
        "default_project": pid,
    }
    write_root_files(target, hub, vendor=a.vendor)
    write_hub_files(target / "projects" / pid, pcfg)
    if not a.no_git:
        r = subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(target),
                           capture_output=True, text=True)
        if r.returncode != 0:
            warn(f"git init 失败：{r.stderr.strip()[:200]}")
    print(f"已生成 pm 仓库: {target}")
    print(f"  项目: {pid}（{name}）")
    print(f"  泳道: {' / '.join(label for _, label in platforms)}")
    print("下一步：")
    print(f"  cd {target}")
    print(f"  python {Path(__file__).resolve()} --roadmap {target} setup --machine <本机名>")
    print("  git add -A && git commit -m 'init roadmap'")
    print("  git remote add origin <私有仓库> && git push -u origin main")
    print("  # 其他机器: clone roadmap 后 setup + bind " + pid)
    print("  # 云端: cron 里 git pull && pm.py --roadmap <dir> build && rsync out/ <静态目录>")
    print("  # 加项目: 复制 projects/<新id>/ 并改 hub.json 的 projects")
    if not a.vendor:
        print("  # 本仓库不含 pm.py；用 --vendor 可复制一份，或始终用 pm 工具仓的 pm.py --roadmap 指向它")


def cmd_setup(a):
    hub = load_hub()
    if a.machine:
        (HUB_ROOT / ".machine").write_text(sanitize(a.machine) + "\n", encoding="utf-8")
    print(f"本机身份: {machine_id()}")
    if (HUB_ROOT / ".git").exists():
        git("config", "pull.rebase", "true")
        hooks = HUB_ROOT / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-merge"
        hook.write_text(HOOK_SH, encoding="utf-8", newline="\n")
        try:
            os.chmod(hook, 0o755)
        except OSError:
            pass
        print("已安装 post-merge hook：pull 后自动 pm build")
    else:
        warn("不是 git 仓库，跳过 hook（先 git init）")
    write_launchers(HUB_ROOT)
    print(f"项目: {', '.join(hub['projects'])}（默认 {hub['default_project']}）")
    print("已生成启动器：Windows 双击 start-pm.cmd，Mac 双击 start-pm.command，Linux 运行 ./start-pm.sh")
    print("检查清单：")
    print("  [ ] git remote add origin <私有仓库>")
    print("  [ ] 其他机器 git clone + pm setup --machine <名字> + pm bind <项目>")
    print("  [ ] 云端 cron: git pull && python3 pm.py build && rsync out/ <静态目录>")


def cmd_change(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}")
    if not re.match(r"^r\d+$", a.rev):
        die("--rev 形如 r1 / r2 / r3")
    plats = [p.strip() for p in a.affects.split(",")] if a.affects else list(cfg["platforms"])
    unknown = [p for p in plats if p not in cfg["platforms"]]
    if unknown:
        die(f"未知平台: {', '.join(unknown)}")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "change",
        "feature": a.feature,
        "value": {
            "rev": a.rev,
            "at": ts[:10],
            "breaking": bool(a.breaking),
            "affects": plats,
            "note": a.note or "",
            "contract": a.contract,
        },
    }
    append_event(ev)
    print(f"已记录变更 {a.feature} → {a.rev}" + ("（破坏性）" if a.breaking else ""))
    print("  受影响: " + " / ".join(plats))
    print(f"  各端跟进后: pm set {a.feature} <platform> done --rev {a.rev}")


# --------------------------------------------------------------------------
# DSH 会话：派工 / 领任务 / 上报
# --------------------------------------------------------------------------
def current_session(cfg: dict, arg=None) -> str:
    return str(arg or session_id())


def parse_task_ref(ref: str, lane, cfg: dict):
    if ":" in str(ref):
        fid, lane2 = str(ref).split(":", 1)
        return fid.strip(), lane2.strip()
    return str(ref).strip(), lane


def deps_met(state: dict, fid: str, lane: str) -> list:
    unmet = []
    cell = state[fid]["platforms"][lane]
    for d in cell.get("depends_on") or []:
        if ":" not in str(d):
            continue
        dfid, dlane = str(d).split(":", 1)
        if dfid in state and dlane in state[dfid]["platforms"]:
            if state[dfid]["platforms"][dlane]["status"] not in DONE_STATES:
                unmet.append(d)
    return unmet


def screen_spec_for(fid: str) -> str:
    d = ROOT / "spec" / "screens"
    if not d.exists():
        return ""
    for p in sorted(d.glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if s.get("feature") == fid:
            return f"spec/screens/{p.name}"
    return ""


def task_brief(cfg: dict, state: dict, fid: str, lane: str) -> str:
    s = state[fid]
    f = s["feature"]
    cell = s["platforms"][lane]
    labels = cfg["lane_labels"]
    plan = f.get("plan") or {}
    ct = f.get("contract") or {}
    lines = [
        f"# 任务：{f['id']} · {f['title']}",
        "",
        f"- 泳道：{labels.get(lane, lane)}",
        f"- 优先级：{f.get('priority', '')} · 迭代：{f.get('iteration', '')}",
        f"- 方案：{plan.get('doc') or '—'}（{'已批准' if plan.get('approved', True) else '未批准'}）",
        f"- 规格：{f.get('spec') or '—'}",
        f"- 契约：{ct.get('version') or '—'}" + ("（已冻结）" if ct.get("frozen") else ""),
        f"- 当前 revision：{f.get('revision') or 'r1'}",
    ]
    spec = screen_spec_for(fid)
    if spec:
        lines.append(f"- 屏幕规格：{spec}（实现前先读）")
    repos = cfg.get("repos") or {}
    if repos:
        lines.append("- 代码仓库：" + " / ".join(
            f"{v.get('name', k)}（{v.get('role', '')}）" for k, v in repos.items()))
    if f.get("acceptance"):
        lines += ["", "## 验收"] + [f"- {x}" for x in f["acceptance"]]
    lines += [
        "",
        "## 工作约定",
        "- 进度上报：`pm report --status doing|review|done --note \"...\"`",
        "- 完成时：`pm report --status done --rev <rN> --note \"...\"`",
        "- 需求变更：`pm change <ID> --rev rN --breaking --note \"...\"`",
        "- 提交前：`pm check`",
    ]
    return "\n".join(lines)


def claim_task(cfg: dict, state: dict, fid: str, lane: str, sess: str, force: bool = False) -> str:
    if fid not in state:
        die(f"需求不存在: {fid}")
    if lane not in state[fid]["platforms"]:
        die(f"泳道不存在: {lane}")
    cell = state[fid]["platforms"][lane]
    if cell["status"] == "na":
        die(f"{fid}:{lane} 不涉及该泳道")
    if cell["status"] == "done":
        die(f"{fid}:{lane} 已完成，无需领取")
    unmet = deps_met(state, fid, lane)
    if unmet and not force:
        die("依赖未完成: " + ", ".join(unmet) + "（确认要继续加 --force）")
    old = cell.get("session")
    if old and old != sess:
        warn(f"该任务原由 {old} 领取，现在转交给 {sess}")
    append_event(_event(cfg, type="claim", feature=fid, platform=lane,
                        value={"session": sess, "at": now_utc()}))
    return task_brief(cfg, state, fid, lane)


def select_next(state: dict, cfg: dict):
    queued = []
    for fid, s in state.items():
        for lane in cfg["lanes"]:
            cell = s["platforms"][lane]
            d = cell.get("dispatch") or {}
            if (d.get("state") == "queued" and not cell.get("session")
                    and cell["status"] in ("todo", "doing")):
                queued.append((d.get("at") or "", fid, lane))
    if queued:
        queued.sort()
        return queued[0][1], queued[0][2], "queue"
    ready = []
    for fid, s in state.items():
        f = s["feature"]
        if f.get("iteration") != cfg["current_iteration"]:
            continue
        if not (f.get("plan") or {}).get("approved", True):
            continue
        for lane in cfg["lanes"]:
            cell = s["platforms"][lane]
            if cell["status"] != "todo" or deps_met(state, fid, lane):
                continue
            ready.append((PRIORITY_ORDER.get(f.get("priority"), 9), fid, lane))
    if not ready:
        return None
    ready.sort()
    return ready[0][1], ready[0][2], "ready"


def queue_rows(state: dict, cfg: dict):
    rows = []
    for fid, s in state.items():
        for lane in cfg["lanes"]:
            cell = s["platforms"][lane]
            d = cell.get("dispatch") or {}
            if d.get("state") == "queued" and not cell.get("session"):
                rows.append((d.get("at") or "", fid, lane, cell["status"],
                             s["feature"].get("title", "")))
    rows.sort()
    return rows


def cmd_dispatch(a):
    cfg = load_cfg()
    feats = load_features()
    state = fold(cfg, feats, read_events())
    fid, lane = parse_task_ref(a.task, a.lane, cfg)
    if fid not in state:
        die(f"需求不存在: {fid}")
    if lane not in state[fid]["platforms"]:
        die(f"泳道不存在: {lane}")
    append_event(_event(cfg, type="dispatch", feature=fid, platform=lane,
                        value={"at": now_utc(), "by": machine_name(cfg),
                               "note": a.note or ""}))
    print(f"已派工：{fid}:{lane}")
    print("在 DSH 会话里说「领任务」，或运行：pm next / pm task " + f"{fid}:{lane}")


def cmd_next(a):
    cfg = load_cfg()
    state = fold(cfg, load_features(), read_events())
    sess = current_session(cfg, a.session)
    pick = select_next(state, cfg)
    if not pick:
        print("没有待认领的派工，也没有可开工任务。")
        return
    fid, lane, source = pick
    print(("[队列] " if source == "queue" else "[可开工] ") + f"领取任务：{fid}:{lane}")
    print(claim_task(cfg, state, fid, lane, sess))
    print(f"\n已领取：{fid}:{lane}  @ {sess}")
    print("完成后：pm report --status done --rev rN --note \"...\"")


def cmd_task(a):
    cfg = load_cfg()
    state = fold(cfg, load_features(), read_events())
    fid, lane = parse_task_ref(a.task, a.lane, cfg)
    if fid not in state:
        die(f"需求不存在: {fid}")
    if not lane:
        options = [p for p in cfg["lanes"]
                   if state[fid]["platforms"][p]["status"] not in ("done", "na")]
        if len(options) == 1:
            lane = options[0]
        else:
            print(f"{fid} 有多个泳道，请指定：")
            for p in options:
                print(f"  {fid}:{p}  {state[fid]['platforms'][p]['status']}")
            return
    print(claim_task(cfg, state, fid, lane, current_session(cfg, a.session), a.force))
    print(f"\n已领取：{fid}:{lane}")


def report_task(cfg: dict, state: dict, fid, lane, sess: str, status=None,
                rev=None, note=None, blocked_by=None, eta=None):
    if not fid:
        found = [(fid, p) for fid, s in state.items() for p in cfg["lanes"]
                 if s["platforms"][p].get("session") == sess]
        if not found:
            die("没有找到本会话领取的任务；请指定 pm report <ID>:<lane> ...")
        if len(found) > 1 and not status:
            die("本会话有多个任务，请指定 pm report <ID>:<lane> ...")
        fid, lane = found[0]
    if fid not in state or lane not in state[fid]["platforms"]:
        die(f"任务不存在: {fid}:{lane}")
    if status:
        ev = _event(cfg, type="status", feature=fid, platform=lane,
                    value=status, session=sess)
        for k, v in (("rev", rev), ("note", note), ("blocked_by", blocked_by), ("eta", eta)):
            if v:
                ev[k] = v
        append_event(ev)
        return fid, lane, f"已上报 {fid}:{lane} → {status}"
    if not note:
        die("需要 --status 或 --note")
    append_event(_event(cfg, type="note", feature=fid, value=f"[{sess}] {note}"))
    return fid, lane, f"已记录备注 → {fid}"


def cmd_report(a):
    cfg = load_cfg()
    state = fold(cfg, load_features(), read_events())
    sess = current_session(cfg, a.session)
    fid, lane = (parse_task_ref(a.task, a.lane, cfg) if a.task else (None, None))
    _, _, msg = report_task(cfg, state, fid, lane, sess, a.status, a.rev, a.note,
                            a.blocked_by, a.eta)
    print(msg)


def cmd_queue(a):
    cfg = load_cfg()
    state = fold(cfg, load_features(), read_events())
    rows = queue_rows(state, cfg)
    if not rows:
        print("派工队列为空。")
        return
    labels = cfg["lane_labels"]
    for at, fid, lane, status, title in rows:
        print(f"{at}  {fid:<16}{labels.get(lane, lane):<7}{status:<8}{title}")


def cmd_plan(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "plan",
        "feature": a.feature,
        "value": {"doc": a.doc, "approved": bool(a.approve), "at": ts[:10]},
    }
    append_event(ev)
    print(f"方案{'已批准' if a.approve else '草稿'}: {a.feature}"
          + (f"（{a.doc}）" if a.doc else ""))


def cmd_assign(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}")
    if a.platform not in cfg["platforms"]:
        die(f"未知泳道: {a.platform}（可用: {', '.join(cfg['platforms'])}）")
    people = cfg.get("people") or {}
    if people and a.person not in people:
        die(f"未知负责人 {a.person}（可用: {', '.join(people)}）")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "assign",
        "feature": a.feature,
        "platform": a.platform,
        "value": a.person,
    }
    append_event(ev)
    print(f"已指派 {a.feature} · {a.platform} → {a.person}")


def cmd_dep(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}")
    if a.lane not in cfg["platforms"]:
        die(f"未知泳道: {a.lane}")
    deps = []
    if not a.clear:
        for d in (a.on or "").split(","):
            d = d.strip()
            if not d:
                continue
            if ":" not in d:
                die("依赖格式为 需求ID:泳道，例如 F-1:backend")
            fid2, lane2 = d.split(":", 1)
            if fid2 not in feats:
                die(f"依赖的需求不存在: {fid2}")
            if lane2 not in cfg["platforms"]:
                die(f"依赖的泳道不存在: {lane2}")
            if f"{fid2}:{lane2}" == f"{a.feature}:{a.lane}":
                die("不能依赖自己")
            deps.append(f"{fid2}:{lane2}")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "dep",
        "feature": a.feature,
        "platform": a.lane,
        "value": deps,
    }
    append_event(ev)
    print(f"依赖已更新 {a.feature}:{a.lane} → {', '.join(deps) if deps else '（无）'}")


def cmd_shots(a):
    hub = load_hub()
    env = os.environ.get("PM_PROJECT", "").strip()
    pids = [env] if env else list(hub["projects"])
    bad = False
    for pid in pids:
        use_project(pid)
        bad = _shots_one(a) or bad
    if bad:
        raise SystemExit(1)


def _shots_one(a):
    cfg = load_cfg()
    lanes = cfg["lanes"]
    labels = cfg["lane_labels"]
    screens_dir = ROOT / cfg.get("screens_dir", "spec/screens")
    shots_dir = ROOT / cfg.get("shots_dir", "shots")
    screens = []
    for p in sorted(screens_dir.glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            warn(f"{p.name} 解析失败: {e}")
            continue
        sid = s.get("screen") or p.stem
        screens.append({"id": sid, "title": s.get("title", sid),
                        "states": list((s.get("states") or {}).keys())})
    rows = []
    for s in screens:
        for st in s["states"]:
            for lane in lanes:
                rel = f"{shots_dir.name}/{lane}/{s['id']}/{st}.png"
                rows.append({"screen": s["id"], "state": st, "lane": lane,
                             "path": rel, "exists": (shots_dir / lane / s["id"] / f"{st}.png").exists()})
    missing = [r for r in rows if not r["exists"]]
    out = out_dir() / "shots"
    out.mkdir(parents=True, exist_ok=True)
    rel_base = os.path.relpath(shots_dir, out).replace("\\", "/")
    (out / "matrix.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html = ["<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<title>跨端截图对齐</title><style>",
            "body{background:#0b1220;color:#e2e8f0;font:13px/1.5 system-ui,sans-serif;margin:0;padding:20px}",
            "h1{font-size:18px;margin:0 0 4px}.sub{color:#94a3b8;font-size:12px;margin-bottom:16px}",
            "table{border-collapse:collapse;width:100%}",
            "th,td{border:1px solid #1e293b;padding:6px;vertical-align:top;text-align:left}",
            "th{background:#111a2b;color:#94a3b8}td.screen{color:#94a3b8;white-space:nowrap;font-size:12px}",
            "img{max-width:220px;border-radius:6px;display:block;border:1px solid #223049}",
            ".miss{color:#f87171;font-size:11px}",
            "</style></head><body><h1>跨端截图对齐</h1>",
            f"<div class='sub'>生成于 {now_utc()} · {len(rows)} 格，缺失 {len(missing)} · "
            f"路径 {shots_dir.name}/&lt;lane&gt;/&lt;screen&gt;/&lt;state&gt;.png</div>",
            "<table><thead><tr><th>屏幕 / 状态</th>"]
    html += [f"<th>{labels.get(l, l)}</th>" for l in lanes]
    html.append("</tr></thead><tbody>")
    for s in screens:
        for st in s["states"]:
            html.append(f"<tr><td class='screen'>{s['title']}<br><span class='miss'>{st}</span></td>")
            for lane in lanes:
                img = shots_dir / lane / s["id"] / f"{st}.png"
                if img.exists():
                    html.append(f"<td><img src='{rel_base}/{lane}/{s['id']}/{st}.png' alt=''></td>")
                else:
                    html.append("<td><span class='miss'>缺</span></td>")
            html.append("</tr>")
    html += ["</tbody></table></body></html>"]
    (out / "index.html").write_text("\n".join(html), encoding="utf-8")
    print(f"[{CURRENT_PID}] 截图对齐: {len(rows)} 格，缺失 {len(missing)} → {out / 'index.html'}")
    if missing and a.check:
        for r in missing[:20]:
            print(f"  缺 {r['path']}")
        if len(missing) > 20:
            print(f"  ... 还有 {len(missing) - 20} 个")
    return bool(missing and a.check)


def cmd_gen(a):
    hub = load_hub()
    env = os.environ.get("PM_PROJECT", "").strip()
    pids = [env] if env else list(hub["projects"])
    did = []
    for pid in pids:
        use_project(pid)
        if a.what in ("all", "tokens"):
            did.append(gen_tokens())
        if a.what in ("all", "screens"):
            did.append(gen_screens())
    if not any(did):
        print("没有可生成的源（design/tokens.json 或 spec/screens/*.json）")


def cmd_check(a):
    hub = load_hub()
    env = os.environ.get("PM_PROJECT", "").strip()
    pids = [env] if env else list(hub["projects"])
    rc = 0
    for pid in pids:
        use_project(pid)
        if len(pids) > 1:
            print(f"\n### {pid}")
        rc |= _check_one()
    return rc


def _check_one():
    cfg = load_cfg()
    feats = load_features()
    problems, warns = [], []
    state = fold(cfg, feats, read_events())
    for fid, s in state.items():
        f = s["feature"]
        revs = f.get("revisions") or []
        seen = set()
        for r in revs:
            rev = r.get("rev")
            if not rev or not re.match(r"^r\d+$", str(rev)):
                problems.append(f"{fid}: revision 格式非法 {rev!r}")
            if rev in seen:
                problems.append(f"{fid}: revision 重复 {rev}")
            seen.add(rev)
        cur = f.get("revision")
        if cur and seen and cur not in seen:
            problems.append(f"{fid}: revision={cur} 不在 revisions 列表里")
        for p, cell in s["platforms"].items():
            if len(revs) > 1 and cell.get("status") in ("done", "review") and not cell.get("rev"):
                warns.append(f"{fid}·{p}: 已完成但没记 --rev，无法判断是否漂移")
    # 依赖 / 负责人 / 方案闸门
    task_keys = {f"{fid}:{p}" for fid in state for p in cfg["platforms"]}
    deps_map = {}
    for fid, s in state.items():
        for p, cell in s["platforms"].items():
            for d in cell.get("depends_on") or []:
                deps_map.setdefault(f"{fid}:{p}", []).append(d)
                if d not in task_keys:
                    problems.append(f"{fid}:{p} 依赖不存在的任务 {d}")
                if d == f"{fid}:{p}":
                    problems.append(f"{fid}:{p} 依赖自己")
    color = {}

    def visit(k, stack):
        color[k] = 1
        for d in deps_map.get(k, []):
            if color.get(d) == 1:
                problems.append("依赖成环: " + " -> ".join(stack + [d]))
            elif color.get(d) is None and d in task_keys:
                visit(d, stack + [d])
        color[k] = 2

    for k in sorted(task_keys):
        if color.get(k) is None:
            visit(k, [k])
    people = cfg.get("people") or {}
    if people:
        for fid, s in state.items():
            for p, cell in s["platforms"].items():
                o = cell.get("owner")
                if o and o not in people:
                    warns.append(f"{fid}·{p}: 负责人 {o} 不在 people 里")
    for fid, s in state.items():
        if s["feature"].get("iteration") != cfg["current_iteration"]:
            continue
        plan = s["feature"].get("plan") or {}
        if not plan.get("approved", True):
            started = [p for p in cfg["platforms"]
                       if s["platforms"][p]["status"] in ("doing", "review", "done")]
            if started:
                warns.append(f"{fid}: 方案未批准但已开工（{'/'.join(started)}）")

    tokens_src = ROOT / cfg.get("tokens_path", "design/tokens.json")
    if not tokens_src.exists():
        warns.append(f"没有 {tokens_src.name}（无法生成各端令牌）")
    else:
        try:
            tok = json.loads(tokens_src.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{tokens_src.name}: JSON 解析失败 {e}")
            tok = {}
        for g, vals in tok.items():
            if not isinstance(vals, dict):
                continue
            for k, v in vals.items():
                if isinstance(v, str) and v.startswith("#") and not re.match(r"^#[0-9A-Fa-f]{6}$", v):
                    problems.append(f"令牌 {g}.{k} 颜色格式应为 #RRGGBB")
    screens_dir = ROOT / cfg.get("screens_dir", "spec/screens")
    screens = sorted(screens_dir.glob("*.json")) if screens_dir.exists() else []
    covered = set()
    for p in screens:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{p.name}: JSON 解析失败 {e}")
            continue
        sid = s.get("screen") or p.stem
        fid = s.get("feature")
        if fid and fid not in feats:
            problems.append(f"{sid}: 引用了不存在的需求 {fid}")
        if fid:
            covered.add(fid)
        states = s.get("states") or {}
        if not states:
            problems.append(f"{sid}: 没有定义任何状态")
        for st, v in states.items():
            if not (v.get("copy") or []):
                problems.append(f"{sid}.{st}: 缺文案")
            if not v.get("assert"):
                problems.append(f"{sid}.{st}: 缺语义断言")
        for k, v in (s.get("copy") or {}).items():
            if not str(v).strip() or "TODO" in str(v) or "待定" in str(v):
                problems.append(f"{sid}: 文案 {k} 为空或占位")
        for pf in (s.get("deviations") or {}):
            if pf not in cfg["platforms"]:
                problems.append(f"{sid}: 差异白名单里的平台 {pf} 未配置")
        if not s.get("assertions"):
            problems.append(f"{sid}: 没有验收断言")
    for fid, f in feats.items():
        if (f.get("iteration") != "backlog" and f.get("priority") == "P0"
                and fid not in covered):
            warns.append(f"{fid}: P0 需求还没有屏幕规格")
    shots_dir = ROOT / cfg.get("shots_dir", "shots")
    if screens and not shots_dir.exists():
        warns.append(f"还没有 {shots_dir.name}/ 截图基线（跑 pm shots 看捕获矩阵）")
    for w_ in warns:
        print(f"警告: {w_}")
    for x in problems:
        print(f"问题: {x}")
    print(f"检查完成：{len(problems)} 个问题 / {len(warns)} 个警告"
          f"（需求 {len(feats)}，屏幕规格 {len(screens)}）")
    return 0 if not problems else 1




def cmd_machine(a):
    f = HUB_ROOT / ".machine"
    if a.name:
        f.write_text(sanitize(a.name) + "\n", encoding="utf-8")
        print(f"本机身份已设为 {sanitize(a.name)}（.machine 不入库，每台机器各设一次）")
    else:
        print(machine_id())


def cmd_bind(a):
    hub = load_hub()
    sess = session_id()
    if a.clear:
        p = bind_path(sess)
        if p.exists():
            p.unlink()
        print("已解除本会话的项目绑定")
        return
    if not a.project:
        p = bind_path(sess)
        if p.exists():
            print(json.loads(p.read_text(encoding="utf-8")).get("project"))
        else:
            print(f"未绑定（可用: {', '.join(hub['projects'])}）")
        return
    if a.project not in hub["projects"]:
        die(f"未知项目: {a.project}（可用: {', '.join(hub['projects'])}）")
    bind_path(sess).write_text(
        json.dumps({"project": a.project, "session": sess, "bound_at": now_utc()},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"本会话（{sess}）已绑定项目: {a.project}")


def cmd_projects(a):
    hub = load_hub()
    sess = session_id()
    cur = bound_project(hub, sess)
    print(f"当前会话: {sess}  绑定: {cur or '未绑定'}")
    print(f"{pad('项目', 18)}{pad('名称', 18)}路径")
    for pid, meta in hub["projects"].items():
        mark = " *" if pid == cur else ""
        print(f"{pad(pid, 18)}{pad(meta['name'], 18)}{meta['path']}{mark}")


def cmd_new(a):
    cfg = load_cfg()
    ensure_layout()
    fid = a.id.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", fid):
        die("需求 ID 只能包含字母/数字/._-，且以字母或数字开头")
    hub = load_hub()
    other = find_project_of_feature(hub, fid)
    if other and CURRENT_PID not in other:
        die(f"需求 ID 已被项目 {other[0]} 使用: {fid}（换一个 ID）")
    path = ROOT / "features" / f"{fid}.json"
    if path.exists():
        die(f"需求已存在: {fid}")
    iterations = {i["id"] for i in cfg["iterations"]}
    it = a.iter or cfg["current_iteration"]
    if it not in iterations:
        warn(f"迭代 {it} 不在 hub.json 的 iterations 里")
    chosen = [p.strip() for p in a.platforms.split(",")] if a.platforms else list(cfg["platforms"])
    unknown = [p for p in chosen if p not in cfg["platforms"]]
    if unknown:
        die(f"未知平台: {', '.join(unknown)}（可用: {', '.join(cfg['platforms'])}）")
    plats = {}
    for p in cfg["platforms"]:
        plats[p] = {"status": "todo" if p in chosen else "na"}
    feat = {
        "id": fid,
        "title": a.title,
        "iteration": it,
        "priority": a.priority,
        "area": a.area or "",
        "spec": a.spec or "",
        "acceptance": a.acceptance or [],
        "contract": {"version": None, "frozen": False, "doc": None},
        "revision": "r1",
        "revisions": [
            {"rev": "r1", "at": now_utc()[:10], "breaking": False, "note": "初版", "contract": None}
        ],
        "platforms": plats,
        "tags": a.tags or [],
        "created_at": now_utc(),
        "project": CURRENT_PID,
    }
    path.write_text(json.dumps(feat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已创建 {fid}: {feat['title']}（{it}，{feat['priority']}）")
    print(f"  features/{fid}.json  → 记得 git add / pm sync")


def cmd_set(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}（用 pm list 查看）")
    if a.platform not in cfg["platforms"]:
        die(f"未知平台: {a.platform}（可用: {', '.join(cfg['platforms'])}）")
    if a.status not in STATUSES:
        die(f"未知状态: {a.status}（可用: {', '.join(STATUSES)}）")
    if a.eta:
        try:
            date.fromisoformat(a.eta)
        except ValueError:
            die("--eta 需要 YYYY-MM-DD")
    if a.status == "blocked" and not a.blocked_by:
        warn("blocked 状态建议用 --blocked-by 写明原因，面板会直接展示")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "status",
        "feature": a.feature,
        "platform": a.platform,
        "value": a.status,
    }
    for k in ("eta", "note", "blocked_by", "owner", "rev"):
        v = getattr(a, k)
        if v:
            ev[k] = v
    append_event(ev)
    print(f"已记录 {a.feature} · {a.platform} → {a.status}")
    print("  pm build 刷新面板，pm sync 推给其他机器")


def cmd_contract(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}")
    if not a.version:
        die("需要 --version（例如 v1.11）")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "contract",
        "feature": a.feature,
        "value": {"version": a.version, "doc": a.doc, "frozen": not a.unfreeze},
    }
    append_event(ev)
    state = "已解冻" if a.unfreeze else "已冻结"
    print(f"契约 {a.version} {state}: {a.feature}")


def cmd_log(a):
    cfg = load_cfg()
    feats = load_features()
    if a.feature not in feats:
        die(f"没有这个需求: {a.feature}")
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
        "type": "note",
        "feature": a.feature,
        "value": a.text,
    }
    append_event(ev)
    print(f"已记录备注 → {a.feature}")


def cmd_list(a):
    hub = load_hub()
    env = os.environ.get("PM_PROJECT", "").strip()
    pids = [env] if env else list(hub["projects"])
    for pid in pids:
        use_project(pid)
        if len(pids) > 1:
            print(f"\n### {pid} · {hub['projects'][pid]['name']}")
        _list_one(a)


def _list_one(a):
    cfg = load_cfg()
    feats = load_features()
    state = fold(cfg, feats, read_events())
    items = sorted(
        state.values(),
        key=lambda s: (
            PRIORITY_ORDER.get(s["feature"].get("priority"), 9),
            s["feature"].get("iteration") != cfg["current_iteration"],
            s["feature"]["id"],
        ),
    )
    if a.iter:
        items = [s for s in items if s["feature"].get("iteration") == a.iter]
    if a.platform:
        items = [s for s in items if s["platforms"].get(a.platform, {}).get("status") != "na"]
    print(f"{pad('ID', 17)}{pad('P', 4)}{pad('R', 5)}{pad('迭代', 10)}"
          + " ".join(pad(short_label(cfg, p), 5) for p in cfg["platforms"])
          + "  契约")
    for s in items:
        f = s["feature"]
        cells = " ".join(
            pad(short_label(cfg, p) + GLYPH.get(s["platforms"][p]["status"], "?"), 5)
            for p in cfg["platforms"]
        )
        ct = s["contract"] or {}
        contract = (ct.get("version") or "—") + ("✓" if ct.get("frozen") else "")
        print(f"{pad(f['id'], 17)}{pad(f.get('priority', ''), 4)}"
              f"{pad(f.get('revision') or '—', 5)}{pad(f.get('iteration', ''), 10)}"
              f"{cells}  {contract}  {f.get('title', '')}")


def cmd_build(a):
    ensure_hub()
    build_all()


def cmd_sync(a):
    if not (HUB_ROOT / ".git").exists():
        die("这不是 git 仓库：先 git init && git remote add origin <repo>")
    remotes = git("remote").stdout.split()
    if remotes:
        r = git("pull", "--rebase", "--autostash")
        if r.returncode != 0:
            warn(f"pull 失败（可能离线），继续本地流程：{r.stderr.strip()[:300]}")
    build_all(quiet=True)
    git("add", "-A")
    if git("status", "--porcelain").stdout.strip():
        msg = f"hub: {machine_id()} {now_utc()}"
        r = git("commit", "-m", msg)
        if r.returncode != 0:
            warn(f"commit 失败：{r.stderr.strip()[:300]}")
        else:
            print(f"已提交：{msg}")
    else:
        print("没有需要提交的变更")
    if remotes:
        r = git("push")
        if r.returncode != 0:
            warn(f"push 失败（离线？稍后再 pm sync）：{r.stderr.strip()[:300]}")
        else:
            print("已推送")


# --------------------------------------------------------------------------
# 远程模式：CLI → 远程 pm 服务（HTTP API）
# --------------------------------------------------------------------------
REMOTE_CMDS = {"next", "task", "report", "dispatch", "queue", "projects", "bind"}


def client_file() -> Path:
    return pm_home() / "client.json"


def client_cfg() -> dict:
    p = client_file()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_server(args=None):
    if args is not None and getattr(args, "server", None):
        return args.server
    env = os.environ.get("PM_URL", "").strip()
    if env:
        return env
    return (client_cfg().get("url") or "").strip() or None


def resolve_token(args=None):
    if args is not None and getattr(args, "token", None):
        return args.token
    return os.environ.get("PM_TOKEN") or client_cfg().get("token")


def remote_call(url, token, method, path, body=None):
    from urllib import error, request
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url.rstrip("/") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        die(f"远程调用失败 {e.code}: {detail}")
    except Exception as e:
        die(f"连不上 {url}: {e}")


def cmd_roadmap(a):
    cfg = client_cfg()
    if a.path:
        p = Path(a.path).expanduser().resolve()
        if not (p / "hub.json").exists():
            die(f"不是 roadmap 仓库（缺 hub.json）: {p}")
        cfg["roadmap"] = str(p)
        f = client_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        print(f"默认 roadmap: {p}")
    else:
        print(cfg.get("roadmap") or "未设置（pm roadmap <path>）")


def cmd_connect(a):
    p = client_file()
    if a.clear:
        if p.exists():
            p.unlink()
        print("已清除本机远程配置")
        return
    cfg = client_cfg()
    if a.url:
        cfg["url"] = a.url.rstrip("/")
    if a.token:
        cfg["token"] = a.token
    if not a.url and not a.token:
        print(json.dumps(cfg, ensure_ascii=False, indent=2) if cfg else "未配置远程地址")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已保存远程配置: {p}")


def local_binding(sess: str):
    p = bind_path(sess)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("project")
    except json.JSONDecodeError:
        return None


def run_remote(cmd, a, url, token):
    sess = session_id()
    if cmd == "bind":
        if a.clear:
            remote_call(url, token, "POST", "/api/bind",
                        {"session": sess, "project": None})
            p = bind_path(sess)
            if p.exists():
                p.unlink()
            print("已解除本会话的项目绑定")
            return
        meta = remote_call(url, token, "GET", f"/api/meta?session={sess}")
        projects = meta.get("projects") or {}
        if not a.project:
            print(meta.get("binding") or f"未绑定（可用: {', '.join(projects)}）")
            return
        if a.project not in projects:
            die(f"未知项目: {a.project}（可用: {', '.join(projects)}）")
        r = remote_call(url, token, "POST", "/api/bind",
                        {"project": a.project, "session": sess})
        print(f"本会话（{sess}）已绑定项目: {r.get('project', a.project)}")
        return
    if cmd == "projects":
        meta = remote_call(url, token, "GET", f"/api/meta?session={sess}")
        projects = meta.get("projects") or {}
        cur = meta.get("binding")
        print(f"远程: {url}  会话: {sess}  绑定: {cur or '未绑定'}")
        for pid, m in projects.items():
            print(f"  {pad(pid, 18)}{m.get('name', pid)}{' *' if pid == cur else ''}")
        return
    project = os.environ.get("PM_PROJECT")
    if cmd == "next":
        r = remote_call(url, token, "POST", "/api/claim",
                        {"project": project, "session": sess, "mode": "next"})
        print(("[队列] " if r.get("source") == "queue" else "[可开工] ")
              + f"领取任务：{r.get('feature')}:{r.get('lane')}")
        print(r.get("brief", ""))
        print(f"\n已领取：{r.get('feature')}:{r.get('lane')}  @ {sess}")
        return
    if cmd == "task":
        ref = a.task
        if "/" in ref:
            project, ref = ref.split("/", 1)
        fid, lane = ((ref.split(":", 1) + [a.lane])[:2] if ":" in ref else (ref, a.lane))
        r = remote_call(url, token, "POST", "/api/claim",
                        {"project": project, "session": sess, "feature": fid,
                         "lane": lane, "force": bool(a.force)})
        print(r.get("brief", ""))
        print(f"\n已领取：{r.get('feature')}:{r.get('lane')}")
        return
    if cmd == "report":
        fid, lane = None, None
        if a.task:
            ref = a.task
            if "/" in ref:
                project, ref = ref.split("/", 1)
            fid, lane = ((ref.split(":", 1) + [a.lane])[:2] if ":" in ref
                         else (ref, a.lane))
        r = remote_call(url, token, "POST", "/api/report", {
            "project": project, "session": sess, "feature": fid, "lane": lane,
            "status": a.status, "rev": a.rev, "note": a.note,
            "blocked_by": a.blocked_by, "eta": a.eta})
        print(r.get("message", "已上报"))
        return
    if cmd == "dispatch":
        ref = a.task
        if "/" in ref:
            project, ref = ref.split("/", 1)
        fid, lane = ((ref.split(":", 1) + [a.lane])[:2] if ":" in ref else (ref, a.lane))
        remote_call(url, token, "POST", "/api/dispatch",
                    {"project": project, "feature": fid, "platform": lane,
                     "note": a.note})
        print(f"已派工：{fid}:{lane}")
        return
    if cmd == "queue":
        from urllib.parse import urlencode
        q = ("?" + urlencode({"project": project})) if project else ""
        r = remote_call(url, token, "GET", "/api/queue" + q)
        rows = r.get("rows") or []
        if not rows:
            print("派工队列为空。")
            return
        for at, fid, lane, status, title in rows:
            print(f"{at}  {fid:<16}{lane:<8}{status:<8}{title}")
        return


# --------------------------------------------------------------------------
# 本地可写面板：localhost HTTP 服务 + JSON API
# --------------------------------------------------------------------------
def _event(cfg, **kw):
    ts = now_utc()
    ev = {
        "id": f"{sanitize(machine_name(cfg))}-{ts}-{secrets.token_hex(3)}",
        "ts": ts,
        "machine": machine_name(cfg),
    }
    ev.update(kw)
    return ev


def ensure_git_remote() -> bool:
    url = (os.environ.get("GIT_REMOTE") or os.environ.get("PM_GIT_REMOTE") or "").strip()
    if not url or not (HUB_ROOT / ".git").exists():
        return False
    remotes = git("remote").stdout.split()
    if "origin" in remotes:
        current = git("remote", "get-url", "origin").stdout.strip()
        if current and current != url:
            git("remote", "set-url", "origin", url)
            print(f"已更新 git remote: origin {url}")
    else:
        r = git("remote", "add", "origin", url)
        if r.returncode:
            warn(f"配置 git remote 失败：{r.stderr.strip()[:200]}")
            return False
        print(f"已配置 git remote: origin {url}")
    return True


def sync_hub(cfg=None) -> dict:
    if not (HUB_ROOT / ".git").exists():
        return {"ok": False, "error": "不是 git 仓库", "messages": []}
    msgs = []
    ensure_git_remote()
    remotes = git("remote").stdout.split()
    has_upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                       "@{u}").returncode == 0
    if remotes and has_upstream:
        r = git("pull", "--rebase", "--autostash")
        if r.returncode:
            msgs.append("pull 失败：" + r.stderr.strip()[:160])
    build_all(quiet=True)
    git("add", "-A")
    committed = False
    if git("status", "--porcelain").stdout.strip():
        r = git("commit", "-m", f"hub: {machine_id()} {now_utc()}")
        if r.returncode:
            msgs.append("commit 失败：" + r.stderr.strip()[:160])
        else:
            committed = True
    pushed = False
    if remotes:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
        r = git("push", "-u", "origin", branch)
        if r.returncode:
            msgs.append("push 失败：" + r.stderr.strip()[:160])
        else:
            pushed = True
    msg = ("已提交" if committed else "无变更")
    msg += (" · 已推送" if pushed else (" · 未推送" if remotes else " · 无远端"))
    return {
        "ok": not msgs,
        "committed": committed,
        "pushed": pushed,
        "message": msg,
        "error": "; ".join(msgs) if msgs else None,
        "messages": msgs,
    }


def meta_info(editable: bool) -> dict:
    hub = load_hub()
    dirty, last = 0, ""
    if (HUB_ROOT / ".git").exists():
        st = git("status", "--porcelain").stdout.strip()
        dirty = len([x for x in st.splitlines() if x.strip()])
        last = git("log", "-1", "--format=%h %s").stdout.strip()
    return {
        "ok": True,
        "editable": editable,
        "machine": machine_id(),
        "dirty": dirty,
        "last_commit": last,
        "root": str(HUB_ROOT),
        "projects": hub["projects"],
        "default_project": hub["default_project"],
    }


def handle_api(path: str, body: dict, cfg: dict) -> dict:
    if path == "/api/sync":
        return sync_hub(cfg)
    feats = load_features()
    lanes = cfg["lanes"]
    if path == "/api/task":
        fid = str(body.get("feature") or "")
        lane = str(body.get("platform") or "")
        status = str(body.get("status") or "")
        if fid not in feats:
            return {"ok": False, "error": f"需求不存在: {fid}"}
        if lane not in lanes:
            return {"ok": False, "error": f"泳道不存在: {lane}"}
        if status not in STATUSES:
            return {"ok": False, "error": f"状态非法: {status}"}
        if status == "blocked" and not body.get("blocked_by"):
            return {"ok": False, "error": "状态为阻塞时需要填写阻塞原因"}
        eta = body.get("eta")
        if eta:
            try:
                date.fromisoformat(str(eta))
            except ValueError:
                return {"ok": False, "error": "ETA 需要 YYYY-MM-DD"}
        ev = _event(cfg, type="status", feature=fid, platform=lane, value=status)
        for k in ("owner", "rev", "eta", "note", "blocked_by"):
            if body.get(k):
                ev[k] = body[k]
        append_event(ev)
        if body.get("depends_on") is not None:
            clean = []
            for d in body.get("depends_on") or []:
                d = str(d).strip()
                if ":" not in d:
                    return {"ok": False, "error": f"依赖格式应为 需求ID:泳道，收到 {d}"}
                dfid, dlane = d.split(":", 1)
                if dfid not in feats or dlane not in lanes:
                    return {"ok": False, "error": f"依赖不存在: {d}"}
                if d == f"{fid}:{lane}":
                    return {"ok": False, "error": "不能依赖自己"}
                clean.append(d)
            append_event(_event(cfg, type="dep", feature=fid, platform=lane, value=clean))
        return {"ok": True}
    if path == "/api/dispatch":
        fid = str(body.get("feature") or "")
        lane = str(body.get("platform") or "")
        if fid not in feats:
            return {"ok": False, "error": f"需求不存在: {fid}"}
        if lane not in lanes:
            return {"ok": False, "error": f"泳道不存在: {lane}"}
        append_event(_event(cfg, type="dispatch", feature=fid, platform=lane,
                            value={"at": now_utc(), "by": machine_name(cfg),
                                   "note": body.get("note") or ""}))
        state = fold(cfg, feats, read_events())
        return {"ok": True, "prompt": task_brief(cfg, state, fid, lane)}
    if path == "/api/plan":
        fid = str(body.get("feature") or "")
        if fid not in feats:
            return {"ok": False, "error": f"需求不存在: {fid}"}
        append_event(_event(cfg, type="plan", feature=fid, value={
            "doc": body.get("doc"),
            "approved": bool(body.get("approved")),
            "at": now_utc()[:10],
        }))
        return {"ok": True}
    if path == "/api/change":
        fid = str(body.get("feature") or "")
        rev = str(body.get("rev") or "")
        if fid not in feats:
            return {"ok": False, "error": f"需求不存在: {fid}"}
        if not re.match(r"^r\d+$", rev):
            return {"ok": False, "error": "版本号形如 r2"}
        append_event(_event(cfg, type="change", feature=fid, value={
            "rev": rev,
            "at": now_utc()[:10],
            "breaking": bool(body.get("breaking")),
            "affects": lanes,
            "note": body.get("note") or "",
            "contract": None,
        }))
        return {"ok": True}
    if path == "/api/feature":
        fid = str(body.get("id") or "").strip()
        title = str(body.get("title") or "").strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", fid):
            return {"ok": False, "error": "ID 只能包含字母/数字/._-"}
        if fid in feats:
            return {"ok": False, "error": f"需求已存在: {fid}"}
        if not title:
            return {"ok": False, "error": "标题不能为空"}
        iteration = str(body.get("iteration") or cfg["current_iteration"])
        priority = str(body.get("priority") or "P1")
        if priority not in PRIORITY_ORDER:
            return {"ok": False, "error": f"优先级非法: {priority}"}
        ensure_layout()
        chosen = body.get("lanes", body.get("platforms"))
        if isinstance(chosen, str):
            chosen = [x.strip() for x in chosen.split(",") if x.strip()]
        if not chosen:
            chosen = list(lanes)
        unknown = [p for p in chosen if p not in lanes]
        if unknown:
            return {"ok": False, "error": f"未知泳道: {', '.join(unknown)}"}
        acceptance = body.get("acceptance") or []
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        plats = {p: {"status": "todo" if p in chosen else "na", "owner": None}
                 for p in lanes}
        feat = {
            "id": fid,
            "title": title,
            "iteration": iteration,
            "priority": priority,
            "area": str(body.get("area") or ""),
            "spec": str(body.get("spec") or ""),
            "acceptance": acceptance,
            "contract": {"version": None, "frozen": False, "doc": None},
            "revision": "r1",
            "revisions": [{"rev": "r1", "at": now_utc()[:10], "breaking": False,
                           "note": "初版", "contract": None}],
            "plan": {"doc": None, "approved": True, "at": now_utc()[:10]},
            "platforms": plats,
            "tags": [],
            "created_at": now_utc(),
            "project": cfg.get("project"),
        }
        (ROOT / "features" / f"{fid}.json").write_text(
            json.dumps(feat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"ok": True}
    return {"ok": False, "error": "未知接口"}


def server_bind_dir() -> Path:
    d = HUB_ROOT / ".pm" / "bindings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_server_binding(sess: str):
    if not sess:
        return None
    p = server_bind_dir() / f"{sanitize(sess)}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("project")
    except json.JSONDecodeError:
        return None


class HubHandler(BaseHTTPRequestHandler):
    editable = True
    token = None
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _send(self, code, data: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _session(self, body=None):
        if body and body.get("session"):
            return str(body["session"])
        from urllib.parse import parse_qs, urlparse
        return (parse_qs(urlparse(self.path).query).get("session") or [None])[0] or ""

    def _project(self, body=None):
        hub = load_hub()
        pid = None
        if body:
            pid = body.get("project")
        if not pid:
            from urllib.parse import parse_qs, urlparse
            pid = (parse_qs(urlparse(self.path).query).get("project") or [None])[0]
        if not pid:
            pid = read_server_binding(self._session(body))
        if pid:
            if pid not in hub["projects"]:
                raise ValueError(f"未知项目: {pid}")
            return pid
        return hub["default_project"]

    def _bind(self, body):
        hub = load_hub()
        project = body.get("project")
        sess = str(body.get("session") or "remote")
        p = server_bind_dir() / f"{sanitize(sess)}.json"
        if not project:
            if p.exists():
                p.unlink()
            return {"ok": True, "project": None, "session": sess}
        if project not in hub["projects"]:
            return {"ok": False, "error": f"未知项目: {project}"}
        p.write_text(json.dumps({"project": project, "session": sess,
                                 "bound_at": now_utc()}, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        return {"ok": True, "project": project, "session": sess}

    def _auth_ok(self):
        if not self.token:
            return True
        if (self.headers.get("Authorization") or "") == "Bearer " + self.token:
            return True
        if f"pm_token={self.token}" in (self.headers.get("Cookie") or ""):
            return True
        from urllib.parse import parse_qs, urlparse
        return self.token in (parse_qs(urlparse(self.path).query).get("token") or [])

    def _project_add(self, body):
        pid = str(body.get("id") or "").strip()
        if not pid:
            return {"ok": False, "error": "id 必填"}
        argv = [sys.executable, str(Path(__file__).resolve()),
                "--roadmap", str(HUB_ROOT), "project", "add", "--id", pid]
        if body.get("name"):
            argv += ["--name", str(body["name"])]
        if body.get("lanes"):
            argv += ["--lanes", str(body["lanes"])]
        if body.get("repos"):
            argv += ["--repos", str(body["repos"])]
        if body.get("iteration"):
            argv += ["--iteration", str(body["iteration"])]
        if body.get("from"):
            argv += ["--from", str(body["from"])]
        if body.get("default"):
            argv.append("--default")
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            return {"ok": False, "error": (err or out or "project add failed")[:1000]}
        return {"ok": True, "output": out}

    def _claim(self, cfg, body):
        state = fold(cfg, load_features(), read_events())
        sess = str(body.get("session") or "remote")
        fid = body.get("feature")
        lane = body.get("lane")
        if fid:
            if not lane:
                options = [p for p in cfg["lanes"]
                           if state.get(fid, {}).get("platforms", {}).get(p, {})
                           .get("status") not in ("done", "na")]
                if len(options) != 1:
                    return {"ok": False, "error": "请指定泳道"}
                lane = options[0]
            brief = claim_task(cfg, state, fid, lane, sess, bool(body.get("force")))
            return {"ok": True, "feature": fid, "lane": lane, "brief": brief}
        pick = select_next(state, cfg)
        if not pick:
            return {"ok": False, "error": "没有待认领的派工，也没有可开工任务"}
        fid, lane, source = pick
        brief = claim_task(cfg, state, fid, lane, sess, bool(body.get("force")))
        return {"ok": True, "feature": fid, "lane": lane, "source": source, "brief": brief}

    def _report(self, cfg, body):
        state = fold(cfg, load_features(), read_events())
        sess = str(body.get("session") or "remote")
        fid, lane, msg = report_task(
            cfg, state, body.get("feature"), body.get("lane"), sess,
            body.get("status"), body.get("rev"), body.get("note"),
            body.get("blocked_by"), body.get("eta"))
        return {"ok": True, "feature": fid, "lane": lane, "message": msg}

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/healthz":
                self._json(200, {"ok": True})
                return
            if path in ("/", "/index.html"):
                with self.lock:
                    docs = build_all(quiet=True)
                    hub = load_hub()
                html = render_index(docs, hub, editable=self.editable, serve=True,
                                    token=self.token or "")
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path.startswith("/p/"):
                pid = path[3:].strip("/")
                with self.lock:
                    use_project(pid)
                    doc = build_one(load_cfg(), quiet=True)
                html = render_dashboard(doc, editable=self.editable, home="/",
                                        token=self.token or "")
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path.startswith("/api/"):
                if not self._auth_ok():
                    return self._json(401, {"ok": False, "error": "未授权：需要 Bearer token"})
                if path == "/api/state":
                    with self.lock:
                        use_project(self._project())
                        doc = build_one(load_cfg(), quiet=True)
                    self._json(200, doc)
                elif path == "/api/meta":
                    meta = meta_info(self.editable)
                    meta["binding"] = read_server_binding(self._session())
                    self._json(200, meta)
                elif path == "/api/queue":
                    with self.lock:
                        use_project(self._project())
                        cfg = load_cfg()
                        rows = queue_rows(fold(cfg, load_features(), read_events()), cfg)
                    self._json(200, {"ok": True, "rows": rows})
                else:
                    self._json(404, {"ok": False, "error": "not found"})
            else:
                self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._json(400, {"ok": False, "error": str(e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._auth_ok():
            return self._json(401, {"ok": False, "error": "未授权：需要 Bearer token"})
        if not self.editable:
            return self._json(403, {"ok": False, "error": "只读模式：不开放写入"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8-sig") or "{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"请求解析失败: {e}"})
        try:
            with self.lock:
                if path == "/api/sync":
                    result = sync_hub()
                elif path == "/api/project/add":
                    result = self._project_add(body)
                elif path == "/api/build":
                    build_all(quiet=True)
                    result = {"ok": True, "message": "built"}
                elif path == "/api/bind":
                    result = self._bind(body)
                else:
                    use_project(self._project(body))
                    cfg = load_cfg()
                    if path == "/api/claim":
                        result = self._claim(cfg, body)
                    elif path == "/api/report":
                        result = self._report(cfg, body)
                    else:
                        result = handle_api(path, body, cfg)
                    if result.get("ok"):
                        build_one(cfg, quiet=True)
            self._json(200, result)
        except SystemExit:
            self._json(400, {"ok": False, "error": "参数错误"})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})


def cmd_serve(a):
    token = a.token or os.environ.get("PM_TOKEN")
    if a.host not in ("127.0.0.1", "localhost"):
        if not a.read_only and not token:
            die("远程写面板必须设置 --token 或 PM_TOKEN")
    hub = ensure_hub()
    if ensure_git_remote():
        try:
            result = sync_hub()
            if not result.get("ok"):
                warn(f"启动时自动同步失败：{result.get('error')}")
        except Exception as e:
            warn(f"启动时自动同步失败：{e}")
    build_all(quiet=True)
    Handler = type("HubHandler", (HubHandler,),
                   {"editable": not a.read_only, "token": token})
    port = a.port
    httpd = ThreadingHTTPServer((a.host, port), Handler)
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '') else a.host}:{port}/"
    mode = "只读" if a.read_only else "可写"
    print(f"项目总览（{mode}）: {url}  (Ctrl+C 退出)")
    print(f"项目: {', '.join(hub['projects'])}")
    if token:
        print("远程写入需要 Bearer token；CLI 用 PM_TOKEN 或 pm connect")
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


def cmd_doctor(a):
    hub = load_hub()
    print(f"仓库: {HUB_ROOT}  ✓  {len(hub['projects'])} 个项目")
    problems = 0
    for pid in hub["projects"]:
        use_project(pid)
        cfg = load_cfg()
        feats = load_features()
        events = read_events()
        print(f"  {pid}: {len(feats)} 需求 / {len(events)} 事件 / {len(cfg['platforms'])} 泳道")
        for fid, f in feats.items():
            for p in (f.get("platforms") or {}):
                if p not in cfg["platforms"]:
                    print(f"    ! {fid} 含未知泳道 {p}")
                    problems += 1
            it = f.get("iteration")
            if it and it not in {i["id"] for i in cfg["iterations"]}:
                print(f"    ! {fid} 的迭代 {it} 不在配置里")
                problems += 1
        unknown = sorted({e.get("feature") for e in events if e.get("feature") not in feats})
        if unknown:
            print(f"    ! 事件引用了不存在的需求: {', '.join(map(str, unknown))}")
            problems += 1
        bad = [e for e in events if e.get("type") == "status" and e.get("value") not in STATUSES]
        if bad:
            print(f"    ! {len(bad)} 个事件的状态值非法")
            problems += 1
    out = HUB_ROOT / "out" / "index.html"
    print(f"总览: {'已生成' if out.exists() else '未生成（跑 pm build）'}")
    if (HUB_ROOT / ".git").exists():
        st = git("status", "--porcelain").stdout.strip()
        print(f"git: {'有未提交变更' if st else '干净'}")
    print("结论: " + ("✓ 没问题" if not problems else f"{problems} 个问题"))
    return 0 if not problems else 1


# --------------------------------------------------------------------------
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    argv = list(sys.argv[1:] if argv is None else argv)
    for i, x in enumerate(argv):
        if x in ("--hub", "--roadmap") and i + 1 < len(argv):
            root = Path(argv[i + 1]).expanduser().resolve()
            os.environ["PM_ROADMAP"] = str(root)
            globals()["HUB_ROOT"] = root
            globals()["ROOT"] = root
            del argv[i:i + 2]
            break
        if x.startswith("--hub=") or x.startswith("--roadmap="):
            root = Path(x.split("=", 1)[1]).expanduser().resolve()
            os.environ["PM_ROADMAP"] = str(root)
            globals()["HUB_ROOT"] = root
            globals()["ROOT"] = root
            del argv[i]
            break
    for i, x in enumerate(argv):
        if x in ("--project", "-p") and i + 1 < len(argv):
            os.environ["PM_PROJECT"] = argv[i + 1]
            del argv[i:i + 2]
            break
        if x.startswith("--project="):
            os.environ["PM_PROJECT"] = x.split("=", 1)[1]
            del argv[i]
            break
    for i, x in enumerate(argv):
        if x == "--server" and i + 1 < len(argv):
            os.environ["PM_URL"] = argv[i + 1]
            del argv[i:i + 2]
            break
        if x.startswith("--server="):
            os.environ["PM_URL"] = x.split("=", 1)[1]
            del argv[i]
            break
        if x == "--local":
            os.environ["PM_LOCAL"] = "1"
            del argv[i]
            break

    roadmap = os.environ.get("PM_ROADMAP", "").strip()
    if not roadmap:
        roadmap = (client_cfg().get("roadmap") or "").strip()
    if roadmap:
        rp = Path(roadmap).expanduser().resolve()
        if (rp / "hub.json").exists():
            globals()["HUB_ROOT"] = rp
            globals()["ROOT"] = rp

    ap = argparse.ArgumentParser(prog="pm", description="多端迭代管理器（零依赖；用 --roadmap 指向 roadmap 仓库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="在当前目录初始化 hub")
    p.add_argument("--name")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scaffold", help="为新项目生成一个 roadmap 仓库")
    p.add_argument("--dir", required=True)
    p.add_argument("--name")
    p.add_argument("--id", default=None, help="项目 id（默认取 name）")
    p.add_argument("--preset", default="multi")
    p.add_argument("--platforms", "--lanes", dest="platforms", default=None,
                   help="id:显示名，逗号分隔（泳道可任意，如 backend:后端,design:设计）")
    p.add_argument("--people", default=None, help="me:我,ai-1:AI Agent 1")
    p.add_argument("--machines", default=None,
                   help="windows-home:家用 Windows,mac-weekend:Mac,cloud:云服务器:ro")
    p.add_argument("--repos", default=None,
                   help="server:my-api:后端+Web,clients:my-app:iOS/Android")
    p.add_argument("--iteration", default=None, help="2026-11:2026-11-02:2026-11-29:目标")
    p.add_argument("--no-git", action="store_true")
    p.add_argument("--vendor", action="store_true",
                   help="把 pm.py 一起复制进 roadmap 仓库（离线用）")
    p.add_argument("--list-presets", action="store_true")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("setup", help="本机初始化：写 .machine + 装 post-merge hook")
    p.add_argument("--machine")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("machine", help="查看/设置本机身份")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_machine)

    p = sub.add_parser("new", help="新建需求")
    p.add_argument("--id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--iter", default=None)
    p.add_argument("--priority", default="P1", choices=list(PRIORITY_ORDER))
    p.add_argument("--area", default=None)
    p.add_argument("--spec", default=None)
    p.add_argument("--platforms", default=None, help="逗号分隔，默认全部平台")
    p.add_argument("--acceptance", action="append", default=None, help="验收点，可重复")
    p.add_argument("--tags", nargs="*", default=None)
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("set", help="更新某需求某平台的状态")
    p.add_argument("feature")
    p.add_argument("platform")
    p.add_argument("status", choices=list(STATUSES))
    p.add_argument("--eta")
    p.add_argument("--note")
    p.add_argument("--blocked-by", dest="blocked_by")
    p.add_argument("--owner")
    p.add_argument("--rev", help="该端已实现到的 revision，如 r2")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("change", help="记录需求变更（新 revision）")
    p.add_argument("feature")
    p.add_argument("--rev", required=True)
    p.add_argument("--breaking", action="store_true")
    p.add_argument("--affects", default=None, help="逗号分隔，默认全部平台")
    p.add_argument("--note")
    p.add_argument("--contract")
    p.set_defaults(func=cmd_change)

    p = sub.add_parser("plan", help="记录方案（方案先行闸门）")
    p.add_argument("feature")
    p.add_argument("--doc")
    p.add_argument("--approve", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("dispatch", help="派工：把任务放进队列等 DSH 会话认领")
    p.add_argument("task", help="F-1:ios 或 F-1（配合 lane）")
    p.add_argument("lane", nargs="?")
    p.add_argument("--note")
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("next", help="DSH 会话领任务：队列优先，其次可开工")
    p.add_argument("--session")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("task", help="DSH 会话领取指定任务")
    p.add_argument("task")
    p.add_argument("lane", nargs="?")
    p.add_argument("--session")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_task)

    p = sub.add_parser("report", help="DSH 会话上报进度（自动带 session）")
    p.add_argument("task", nargs="?")
    p.add_argument("lane", nargs="?")
    p.add_argument("--status", choices=list(STATUSES))
    p.add_argument("--rev")
    p.add_argument("--note")
    p.add_argument("--blocked-by", dest="blocked_by")
    p.add_argument("--eta")
    p.add_argument("--session")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("queue", help="查看派工队列")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("assign", help="指派某需求某泳道的负责人")
    p.add_argument("feature")
    p.add_argument("platform")
    p.add_argument("person")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("dep", help="设置任务依赖（需求ID:泳道）")
    p.add_argument("feature")
    p.add_argument("lane")
    p.add_argument("--on", default=None, help="逗号分隔，如 F-1:backend,F-2:web")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_dep)

    p = sub.add_parser("shots", help="生成跨端截图对齐页 / 检查缺失")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_shots)

    p = sub.add_parser("contract", help="冻结/解冻某需求的接口契约")
    p.add_argument("feature")
    p.add_argument("--version", required=True)
    p.add_argument("--doc")
    p.add_argument("--unfreeze", action="store_true")
    p.set_defaults(func=cmd_contract)

    p = sub.add_parser("log", help="给需求追加一条备注")
    p.add_argument("feature")
    p.add_argument("text")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("list", help="终端里看需求矩阵")
    p.add_argument("--iter")
    p.add_argument("--platform")
    p.set_defaults(func=cmd_list)

    sub.add_parser("build", help="折叠事件 → 面板/文档/快照").set_defaults(func=cmd_build)
    sub.add_parser("sync", help="pull → build → commit → push").set_defaults(func=cmd_sync)

    p = sub.add_parser("serve", help="打开本地可写面板（双击启动器等价）")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    p.add_argument("--read-only", dest="read_only", action="store_true",
                   help="只读模式（不暴露写入 API）")
    p.add_argument("--token", default=None, help="远程写入 token（也可用 PM_TOKEN）")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("gen", help="生成令牌 / 屏幕规格（build 会自动调用）")
    p.add_argument("what", nargs="?", default="all", choices=["all", "tokens", "screens"])
    p.set_defaults(func=cmd_gen)

    sub.add_parser("check", help="静态检查：revision / 令牌 / 屏幕规格").set_defaults(func=cmd_check)

    sub.add_parser("doctor", help="自检").set_defaults(func=cmd_doctor)

    p = sub.add_parser("roadmap", help="查看/设置默认 roadmap 仓库路径")
    p.add_argument("path", nargs="?")
    p.set_defaults(func=cmd_roadmap)

    p = sub.add_parser("connect", help="配置远程 pm 地址（保存到 ~/.pm/client.json）")
    p.add_argument("url", nargs="?")
    p.add_argument("--token")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("bind", help="把当前 DSH 会话绑定到项目")
    p.add_argument("project", nargs="?")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("projects", help="列出所有项目")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("project", help="项目管理：add / list")
    sp = p.add_subparsers(dest="action", required=True)
    pa = sp.add_parser("add", help="新增项目（roadmap 为空时自动建 hub）")
    pa.add_argument("--id", required=True)
    pa.add_argument("--name")
    pa.add_argument("--lanes", help="逗号分隔，如 backend,web,ios,android,hos")
    pa.add_argument("--repos", help="name:仓库名:角色，逗号分隔")
    pa.add_argument("--iteration")
    pa.add_argument("--from", dest="from_dir", help="导入已有项目目录（如备份）")
    pa.add_argument("--default", action="store_true")
    pa.set_defaults(func=cmd_project)
    pl = sp.add_parser("list", help="列出项目")
    pl.set_defaults(func=cmd_project)

    a = ap.parse_args(argv)
    cmd = a.cmd
    url = None if os.environ.get("PM_LOCAL") else resolve_server()
    if url and cmd in REMOTE_CMDS:
        run_remote(cmd, a, url, resolve_token())
        return
    global_cmds = {"scaffold", "init", "setup", "machine", "bind", "projects",
                   "serve", "sync", "build", "list", "check", "doctor", "gen", "shots",
                   "connect", "roadmap", "project"}
    hub = None
    if cmd not in global_cmds:
        hub = load_hub()
    pid = None
    if cmd in ("task", "dispatch"):
        ref = getattr(a, "task", "") or ""
        if "/" in ref:
            pid, rest = ref.split("/", 1)
            setattr(a, "task", rest)
    if cmd not in global_cmds and pid is None:
        feature = None
        if cmd in ("set", "change", "contract", "plan", "assign", "dep", "log"):
            feature = getattr(a, "feature", None)
        elif cmd in ("task", "dispatch"):
            feature = (getattr(a, "task", "") or "").split(":", 1)[0] or None
        pid = resolve_project(hub, feature=feature)
    if pid:
        use_project(pid)
    rc = a.func(a)
    if isinstance(rc, int):
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
