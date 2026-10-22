#!/usr/bin/env python3
"""成果门户 · 静态站构建器

数据源：GitHub 组织（成员 / 邀请 / 仓库 / 协作者 / 目录树）
       + ROSTER_JSON（姓名·分组·选题·账号映射，走 secret，不落公开仓）

输出：仓库根目录 index.html（单文件，无外部依赖）

隐私边界：公开产物不含 GitHub 用户名、不含仓库名、不含分数。
"""
import json, os, re, subprocess, sys, pathlib, datetime

ORG = os.environ.get("PORTAL_ORG", "shjz-ai-course202610")
ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT.parent.parent / "index.html"
TEMPLATE = ROOT / "template.html"

# ---------- GitHub ----------
def gh_list(path):
    r = subprocess.run(["gh", "api", "--paginate", "--slurp", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[warn] {path}: {r.stderr.strip()[:200]}", file=sys.stderr)
        return []
    try:
        data = json.loads(r.stdout)
    except Exception:
        return []
    if isinstance(data, list) and data and isinstance(data[0], list):
        return [x for page in data for x in page]
    return data if isinstance(data, list) else []

def gh_one(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None

# ---------- 证据模式 ----------
INDICATORS = [
    ("V1", "问题真实性",   r"prd|需求|problem"),
    ("V2", "可量化收益",   r"prd|需求|收益|metric"),
    ("V3", "边界与克制",   r"边界|范围|不做|won.?t|scope"),
    ("I1", "参考与借鉴",   r"竞品|参考|调研|compar|benchmark"),
    ("I2", "技术路线取舍", r"架构|方案|技术选型|architect|design"),
    ("I3", "企业约束适配", r"约束|合规|权限|constraint"),
    ("Q1", "测试完整性",   r"测试|test|用例|case"),
    ("Q2", "可复现性",     r"^readme\.md$"),
    ("Q3", "诚实度与异常", r"已知|限制|问题|limitation|known|caveat"),
    ("E1", "五条标准覆盖", r"自查|验收|checklist|标准|review"),
    ("E2", "界面四要素",   r"线框|原型|wireframe|截图|mock|界面"),
    ("E3", "30秒可懂",     r"演示|demo|说明|介绍"),
]
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".java",
            ".go", ".rs", ".sql", ".sh", ".vue", ".kt", ".swift", ".c",
            ".cpp", ".h", ".ipynb")

def analyze_tree(tree):
    paths = [e["path"] for e in tree if e.get("type") == "blob"]
    low = [p.lower() for p in paths]
    daily = sum(1 for p in paths if re.match(r"^日报/.+\.md$", p))
    output = sum(1 for p in paths if p.startswith("产出包/") and p != "产出包/README.md")
    code = sum(1 for p in low if p.endswith(CODE_EXT))
    hits = {}
    for key, _, pat in INDICATORS:
        rx = re.compile(pat, re.I)
        n = sum(1 for p in low if rx.search(pathlib.PurePosixPath(p).name))
        if key == "E2":
            n = max(n, sum(1 for p in low if p.endswith(IMAGE_EXT)))
        hits[key] = n > 0
    return {"files": len(paths), "daily": daily, "output": output,
            "code": code, "images": sum(1 for p in low if p.endswith(IMAGE_EXT)),
            "evidence": hits}

# ---------- 采集 ----------
def collect():
    members = {u["login"].lower() for u in gh_list(f"orgs/{ORG}/members?per_page=100")}
    invs = gh_list(f"orgs/{ORG}/invitations?per_page=100")
    invmap = {i["login"].lower(): i.get("created_at", "") for i in invs}
    owners = {o["login"].lower() for o in gh_list(f"orgs/{ORG}/members?role=admin&per_page=100")}

    repos = gh_list(f"orgs/{ORG}/repos?per_page=100")
    owner_repo = {}
    repo_stats = {}
    for r in repos:
        name = r["name"]
        admins = gh_one(f'repos/{ORG}/{name}/collaborators?affiliation=direct_permission') or []
        cands = [a["login"].lower() for a in admins
                 if a.get("role_name") == "admin" and a["login"].lower() not in owners
                 and not a["login"].endswith("-bot")]
        if not cands:
            continue                      # 基础设施仓，不属于任何学员
        stu = cands[0]
        if r.get("size") == 0:
            st = {"files": 0, "daily": 0, "output": 0, "code": 0, "images": 0,
                  "evidence": {k: False for k, _, _ in INDICATORS}}
        else:
            t = gh_one(f'repos/{ORG}/{name}/git/trees/{r.get("default_branch", "main")}?recursive=1')
            if not t or "tree" not in t:
                st = {"files": 0, "daily": 0, "output": 0, "code": 0, "images": 0,
                      "evidence": {k: False for k, _, _ in INDICATORS}}
            else:
                st = analyze_tree(t["tree"])
        st["private"] = bool(r.get("private"))
        st["pushed_at"] = r.get("pushed_at") or ""
        repo_stats[name] = st
        owner_repo.setdefault(stu, []).append(name)

    return members, invmap, owner_repo, repo_stats

# ---------- 组装 ----------
def main():
    raw = os.environ.get("ROSTER_JSON", "")
    if raw:
        roster = json.loads(raw)
    else:
        roster = json.loads((ROOT / "roster.json").read_text())

    members, invmap, owner_repo, repo_stats = collect()
    ALIAS = {"yanghansqm-atl": "yanghansqm-alt"}

    people, groups = [], {}
    ind_count = {k: 0 for k, _, _ in INDICATORS}
    tot = {"in_org": 0, "invited": 0, "check": 0, "no_account": 0,
           "has_repo": 0, "has_output": 0, "has_daily": 0, "files": 0}

    for p in roster:
        u = ALIAS.get((p.get("user") or "").strip().lower(), (p.get("user") or "").strip().lower())
        if not u:
            status = "no_account"
        elif u in members:
            status = "in_org"
        elif u in invmap:
            status = "invited"
        else:
            status = "check"
        tot[status] += 1

        mine = owner_repo.get(u, [])
        files = sum(repo_stats[n]["files"] for n in mine)
        daily = sum(repo_stats[n]["daily"] for n in mine)
        output = sum(repo_stats[n]["output"] for n in mine)
        code = sum(repo_stats[n]["code"] for n in mine)
        images = sum(repo_stats[n]["images"] for n in mine)
        ev = {}
        for k, _, _ in INDICATORS:
            ev[k] = any(repo_stats[n]["evidence"].get(k) for n in mine)
        for k in ind_count:
            if ev[k]:
                ind_count[k] += 1
        pushed = max((repo_stats[n]["pushed_at"] for n in mine), default="")

        if mine:
            tot["has_repo"] += 1
        if output:
            tot["has_output"] += 1
        if daily:
            tot["has_daily"] += 1
        tot["files"] += files

        g = p.get("group") or "未分组"
        gd = groups.setdefault(g, {"name": g, "people": 0, "in_org": 0,
                                   "has_repo": 0, "has_output": 0, "files": 0, "daily": 0})
        gd["people"] += 1
        gd["in_org"] += 1 if status == "in_org" else 0
        gd["has_repo"] += 1 if mine else 0
        gd["has_output"] += 1 if output else 0
        gd["files"] += files
        gd["daily"] += daily

        people.append({
            "name": p.get("name", ""), "group": g,
            "code": p.get("code", ""), "topic": p.get("topic", ""),
            "status": status,
            "repos": len(mine), "files": files, "daily": daily, "output": output,
            "code_files": code, "images": images,
            "ev": ev, "evidence": sum(1 for v in ev.values() if v),
            "pushed": pushed[:16].replace("T", " "),
        })

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    data = {
        "org": ORG,
        "snapshot_at": now.strftime("%Y-%m-%d %H:%M"),
        "totals": {"roster": len(roster),
                   "has_account": sum(1 for p in roster if p.get("user")),
                   **tot},
        "groups": sorted(groups.values(), key=lambda x: (-x["people"], x["name"])),
        "indicators": [{"key": k, "label": l, "people": ind_count[k]} for k, l, _ in INDICATORS],
        "people": people,
    }

    tpl = TEMPLATE.read_text()
    html = tpl.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    OUT.write_text(html)
    print(f"OUT {OUT} {OUT.stat().st_size} bytes")
    print("TOTALS", json.dumps(data["totals"], ensure_ascii=False))
    print("REPOS", len(repo_stats), "GROUPED", len(groups))

if __name__ == "__main__":
    main()
