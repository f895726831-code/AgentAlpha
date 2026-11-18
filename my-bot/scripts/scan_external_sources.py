#!/usr/bin/env python3
"""
Track B: 外部前沿扫描脚本

按扫描清单执行各 Tier 的月度/季度扫描，输出候选发现。
不自动入库（需要人工/LLM 审核），仅输出到 external_scans/ 目录。

用法:
  python scripts/scan_external_sources.py --tier 2          # 月度 Tier 2 扫描
  python scripts/scan_external_sources.py --tier 1 --conf neurips  # 会议季扫描
  python scripts/scan_external_sources.py --tier 3 --source twitter  # 社媒扫描
  python scripts/scan_external_sources.py --list-targets     # 列出所有扫描目标
"""

import argparse
import json
import os
import sys
import io
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
EVOLUTION_DIR = PROJECT_ROOT / "data/evolution"
SCANS_DIR = EVOLUTION_DIR / "external_scans"
CHECKLIST_FILE = EVOLUTION_DIR / "external_scan_checklist.md"

# ─── 扫描目标定义 ──────────────────────────────────────────────────────────

TIER1_CONFERENCES = [
    {"name": "ICLR", "field": "ML / Quantitative Finance", "next": "2027 Q1",
     "keywords": "quantitative finance, alpha factor, portfolio optimization, regime switching, limit order book"},
    {"name": "NeurIPS", "field": "ML", "next": "2026-12",
     "keywords": "time series forecasting, financial ML, trading agent, reinforcement learning finance"},
    {"name": "ICML", "field": "ML", "next": "2027-07",
     "keywords": "generative model finance, diffusion model trading, factor mining"},
    {"name": "KDD", "field": "Data Mining / Applications", "next": "2027-08",
     "keywords": "financial data mining, fraud detection, market prediction"},
    {"name": "ICAIF", "field": "AI in Finance", "next": "2026-11",
     "keywords": "AI finance, algorithmic trading, portfolio management"},
    {"name": "FinNLP", "field": "NLP in Finance", "next": "2026-11",
     "keywords": "financial text, sentiment analysis, ESG NLP"},
]

TIER2_JOURNALS = [
    {"name": "Journal of Financial Economics (JFE)", "url": "https://www.sciencedirect.com/journal/journal-of-financial-economics"},
    {"name": "Journal of Portfolio Management (JPM)", "url": "https://jpm.pm-research.com/"},
    {"name": "Review of Financial Studies (RFS)", "url": "https://academic.oup.com/rfs"},
    {"name": "Journal of Quantitative Finance", "url": "https://www.tandfonline.com/toc/rquf20/current"},
]

TIER2_TOOLS = [
    {"name": "Qlib (Microsoft)", "github": "microsoft/qlib", "check": "new releases, new models"},
    {"name": "RD-Agent (Microsoft)", "github": "microsoft/RD-Agent", "check": "new factor discovery methods"},
    {"name": "AlphaBench", "github": "CityU-MLO/AlphaBench", "check": "new baselines, evaluation methods"},
    {"name": "Papers With Backtest", "url": "https://paperswithbacktest.com/", "check": "A-share coverage, new features"},
    {"name": "JKP Factors", "url": "https://jkpfactors.com/", "check": "new factors, new countries"},
    {"name": "AQR Datasets", "url": "https://www.aqr.com/Insights/Datasets", "check": "new datasets"},
]

TIER3_SOCIAL = [
    {"platform": "Twitter/X", "query": "#quantitative finance", "frequency": "weekly"},
    {"platform": "Twitter/X", "query": "#iclr2026 finance", "frequency": "conference season"},
    {"platform": "Reddit", "query": "r/algotrading China OR A-share", "frequency": "weekly"},
    {"platform": "Reddit", "query": "r/quant factor investing", "frequency": "weekly"},
]

TIER3_BROKERAGE = [
    {"firm": "中金公司", "focus": "因子挖掘/多因子模型"},
    {"firm": "华泰证券", "focus": "金工/量化策略"},
    {"firm": "中信建投", "focus": "衍生品/组合管理"},
    {"firm": "国泰君安", "focus": "大类资产配置"},
]


def check_web_search_available() -> bool:
    """Check if we can do web searches (this script is mainly for local cataloging)."""
    return True  # WebSearch is available in the Claude environment


def generate_scan_template(tier: int, source_name: str, scan_type: str = "paper") -> str:
    """Generate a scan template Markdown file with search prompts ready to use."""
    today = datetime.now().strftime("%Y-%m-%d")
    slug = f"{source_name.lower().replace(' ', '-').replace('/', '-')}-{today}"

    templates = {
        "conference": f"""---
source: {source_name}
type: conference_scan
tier: {tier}
scan_date: {today}
status: template
---
# {source_name} 论文扫描 — {today}

## 搜索提示词

将以下提示词逐个输入 WebSearch：

1. `"{source_name} 2026 quantitative finance OR algorithmic trading papers"`
2. `"site:arxiv.org {source_name} 2026 factor model OR portfolio optimization"`
3. `"site:iclr.cc {source_name} 2026 finance workshop papers"`（如适用）

## 候选论文清单

| # | 标题 | 链接 | 与FM_trade关联 | 四维评分 R/F/N/I |
|---|------|------|----------------|-------------------|
| 1 | (搜索后填写) | | | /5 /5 /5 /5 |

## 扫描结论

(搜索完成后填写)

## 推荐进入 Evaluate 的候选

- (筛选后填写)
""",
        "tool": f"""---
source: {source_name}
type: tool_scan
tier: {tier}
scan_date: {today}
status: template
---
# {source_name} 工具更新扫描 — {today}

## 检查项

- [ ] 新版本发布？
- [ ] 新功能/新模型？
- [ ] A股/中国市场支持？
- [ ] 与 FM_trade 的潜在集成点？

## 发现

(检查后填写)

## 四维评分

- Relevance: /5
- Feasibility: /5
- Novelty: /5
- Impact: /5
- Composite: /5
""",
        "journal": f"""---
source: {source_name}
type: journal_scan
tier: {tier}
scan_date: {today}
status: template
---
# {source_name} 新刊扫描 — {today}

## 本期/本季目录

| # | 标题 | 作者 | 与FM_trade关联 |
|---|------|------|----------------|
| 1 | (搜索后填写) | | |

## 值得深读的论文

- (筛选后填写)
""",
    }

    return templates.get(scan_type, templates["conference"])


def run_scan(args):
    """Execute a scan for the given tier and generate template files."""
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    templates_generated = []

    if args.tier == 1:
        # Conference scan
        confs = TIER1_CONFERENCES
        if args.conf:
            confs = [c for c in confs if c["name"].lower() == args.conf.lower()]
            if not confs:
                print(f"Unknown conference: {args.conf}")
                print(f"Available: {[c['name'] for c in TIER1_CONFERENCES]}")
                return

        for conf in confs:
            content = generate_scan_template(1, conf["name"], "conference")
            # Add conference-specific search prompts
            content += f"""
## 关键词

`{conf['keywords']}`

## 搜索提示

1. 使用 WebSearch 工具搜索上述关键词组合
2. 重点关注: 有公开代码的论文，方法描述足够详细可自行实现的论文
3. 过滤: 纯加密货币（方法论可迁移的除外）、纯美股 microstructure（A股不可迁移）
"""
            filename = f"conference-{conf['name'].lower().replace(' ', '-')}-{today}.md"
            filepath = SCANS_DIR / filename
            filepath.write_text(content, encoding="utf-8")
            templates_generated.append({"name": conf["name"], "file": str(filepath)})
            print(f"📄 {conf['name']} → {filepath}")

    elif args.tier == 2:
        # Journals
        if not args.tools_only:
            for journal in TIER2_JOURNALS:
                content = generate_scan_template(2, journal["name"], "journal")
                filename = f"journal-{journal['name'].split('(')[0].strip().lower().replace(' ', '-')}-{today}.md"
                filepath = SCANS_DIR / filename
                filepath.write_text(content, encoding="utf-8")
                templates_generated.append({"name": journal["name"], "file": str(filepath)})
                print(f"📄 {journal['name']} → {filepath}")

        # Tools
        if not args.journals_only:
            for tool in TIER2_TOOLS:
                content = generate_scan_template(2, tool["name"], "tool")
                filename = f"tool-{tool['name'].lower().replace(' ', '-').replace('(', '').replace(')', '')}-{today}.md"
                filepath = SCANS_DIR / filename
                filepath.write_text(content, encoding="utf-8")
                templates_generated.append({"name": tool["name"], "file": str(filepath)})
                print(f"📄 {tool['name']} → {filepath}")

    elif args.tier == 3:
        if not args.brokerage_only:
            for social in TIER3_SOCIAL:
                content = f"""---
source: {social['platform']}: {social['query']}
type: social_scan
tier: 3
scan_date: {today}
status: template
---
# {social['platform']} 扫描 — {social['query']}

频率: {social['frequency']}

## 搜索提示词

使用 WebSearch: `"{social['query']} after:{ (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')}"`

## 发现

(搜索后填写)

## 四维评分

- Relevance: /5
- Feasibility: /5
- Novelty: /5
- Impact: /5
"""
                filename = f"social-{social['platform'].lower().replace('/', '-')}-{today}.md"
                filepath = SCANS_DIR / filename
                filepath.write_text(content, encoding="utf-8")
                templates_generated.append({"name": f"{social['platform']}: {social['query']}", "file": str(filepath)})
                print(f"📄 {social['platform']} → {filepath}")

        if not args.social_only:
            for broker in TIER3_BROKERAGE:
                content = f"""---
source: {broker['firm']} 金工研报
type: brokerage_scan
tier: 3
scan_date: {today}
status: template
---
# {broker['firm']} — {broker['focus']}

## 搜索提示词

使用 WebSearch: `"{broker['firm']} 金工 量化 研报 2026"`

## 发现

(搜索后填写)

## 四维评分

- Relevance: /5
- Feasibility: /5
- Novelty: /5
- Impact: /5
"""
                filename = f"brokerage-{broker['firm']}-{today}.md"
                filepath = SCANS_DIR / filename
                filepath.write_text(content, encoding="utf-8")
                templates_generated.append({"name": broker["firm"], "file": str(filepath)})
                print(f"📄 {broker['firm']} → {filepath}")

    else:
        print(f"Unknown tier: {args.tier}")
        return

    # Generate summary
    summary_file = SCANS_DIR / f"scan-summary-{today}.md"
    summary_lines = [
        f"# Track B 扫描摘要 — {today}",
        f"",
        f"Tier: {args.tier} | 生成模板: {len(templates_generated)} 个",
        f"",
        "## 下一步",
        "",
        "每个模板文件包含现成的搜索提示词。将文件内容逐个交给 LLM Agent，",
        "让它执行 WebSearch 并填写发现。",
        "",
        "填写完成后，对评分 ≥ 3.5 的发现执行:",
        "```bash",
        "python scripts/evolution_capture.py capture \\",
        "  --track external \\",
        "  --source '<来源>' \\",
        "  --title '<标题>' \\",
        "  --composite-score <分>",
        "```",
        "",
        "## 模板文件",
        "",
    ]
    for t in templates_generated:
        summary_lines.append(f"- [{t['name']}]({Path(t['file']).name})")
    summary_file.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"\n📋 摘要: {summary_file}")

    # Update checklist
    _update_checklist(today, args.tier)


def _update_checklist(scan_date: str, tier: int):
    """Update the scan checklist with the latest scan date."""
    if not CHECKLIST_FILE.exists():
        return
    content = CHECKLIST_FILE.read_text(encoding="utf-8")
    # Add a scan record line at the end
    record = f"\n| {scan_date} | Tier {tier} 扫描 | — |"
    with open(CHECKLIST_FILE, "a", encoding="utf-8") as f:
        f.write(record)


def list_targets():
    """Print all scan targets."""
    print("=== Tier 1: Conferences ===")
    for c in TIER1_CONFERENCES:
        print(f"  {c['name']} ({c['field']}) — Next: {c['next']}")

    print("\n=== Tier 2: Journals ===")
    for j in TIER2_JOURNALS:
        print(f"  {j['name']}")

    print("\n=== Tier 2: Tools ===")
    for t in TIER2_TOOLS:
        print(f"  {t['name']} ({t.get('github', t.get('url', 'N/A'))})")

    print("\n=== Tier 3: Social ===")
    for s in TIER3_SOCIAL:
        print(f"  {s['platform']}: {s['query']} ({s['frequency']})")

    print("\n=== Tier 3: Brokerage ===")
    for b in TIER3_BROKERAGE:
        print(f"  {b['firm']} — {b['focus']}")


def main():
    parser = argparse.ArgumentParser(description="Track B 外部前沿扫描")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="扫描 Tier")
    parser.add_argument("--conf", help="指定会议名称 (Tier 1)")
    parser.add_argument("--tools-only", action="store_true", help="仅扫描工具 (Tier 2)")
    parser.add_argument("--journals-only", action="store_true", help="仅扫描期刊 (Tier 2)")
    parser.add_argument("--social-only", action="store_true", help="仅扫描社媒 (Tier 3)")
    parser.add_argument("--brokerage-only", action="store_true", help="仅扫描券商研报 (Tier 3)")
    parser.add_argument("--list-targets", action="store_true", help="列出所有扫描目标")
    args = parser.parse_args()

    if args.list_targets:
        list_targets()
        return

    if args.tier:
        run_scan(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()