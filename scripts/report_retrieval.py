"""Generate the retrieval evaluation report from the synthetic fixture.

This script never opens the production database: fixture regression and real-library
inspection are deliberately separate jobs (see scripts/audit_memory.py for the latter).

Reported configurations, from what a client does today to the candidate v0.3 behaviour:

* question_baseline  - the natural-language question sent as-is, strict keywords
* baseline           - the same keywords the improved path uses, but no fusion
* strict_variants    - query + query_variants with RRF (the v0.2 recommendation)
* auto_keywords_only - search_mode="auto" with the raw phrase and no variants
* auto_with_variants - search_mode="auto" with the keywords (candidate v0.3 recommendation)

Cases are split by how they were used, and the split is stated in the report:

* set=tuning      - the v0.2 cases that query_variants and RRF were designed against.
* set=development - cases that shaped the v0.3 fallback (A01 drove the unusable-fragment
                    rule), so they are not held out and are never described as such.
* set=validation  - cases written after the fallback was frozen. The developer reads their
                    results, so they are validation only, never blind held-out.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from eval_harness import build_store, evaluate, load_fixture, summarize_by_set

from personal_memory.models import Search
from personal_memory.retrieval import (
    FALLBACK_CANDIDATES,
    FALLBACK_MAX_FRAGMENTS,
    GENERIC_DF_MIN,
    GENERIC_DF_RATIO,
    MIN_COVERAGE_FRAGMENTS,
)

CONFIGURATIONS = (
    ("question_baseline", "问题原文（严格）"),
    ("baseline", "关键词（严格，无 variants）"),
    ("improved", "关键词 + variants（严格，v0.2 推荐）"),
    ("auto_keywords_only", "原文 + auto（无 variants）"),
    ("auto_with_variants", "关键词 + variants + auto（v0.3 候选）"),
)


def percent(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def summary_table(results: dict, case_set: str, label: str) -> list[str]:
    lines = [
        f"### {label}",
        "",
        "| 配置 | Recall@5 | Precision@5 | 首条正确率 | 无答案误召回率 | 空结果用例 | 宽松标记记录 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, name in CONFIGURATIONS:
        agg = summarize_by_set(results[key], case_set)
        lines.append(
            f"| {name} | {percent(agg['recall_at_k'])} | {percent(agg['precision_at_k'])} | "
            f"{percent(agg['first_result_accuracy'])} | {percent(agg['no_answer_false_recall_rate'])} | "
            f"{agg['empty_result_cases']} | {agg['relaxed_records']} |"
        )
    agg = summarize_by_set(results["baseline"], case_set)
    lines += [
        "",
        (
            f"样本与分母：用例 {agg['cases']} 条；有相关记录 {agg['cases_with_relevant']} 条"
            f"（Recall@5 与 Precision@5 的分母）；无答案 {agg['no_answer_cases']} 条"
            f"（误召回率的分母）；首条正确率的分母为全部 {agg['cases']} 条。"
        ),
        (
            f"其中未召回任何相关记录的用例 {agg['cases_with_relevant'] - agg['cases_with_hits']} 条，"
            f"返回空结果的用例 {agg['empty_result_cases']} 条，"
            f"误召回记录数 {agg['false_positive_records']} 条，"
            f"命中禁止记录数 {agg['unexpected_returned']} 条。"
        ),
        "",
    ]
    return lines


def before_after_table(results: dict, case_set: str) -> list[str]:
    lines = [
        f"### 逐条前后对比（{case_set}）",
        "",
        "严格与 auto 使用同一组 query；auto 只在严格候选池为空时执行一次宽松回退。",
        "",
        "| 用例 | 问题 | query | 严格 Top1 | auto Top1 | 严格 Recall@5 | auto Recall@5 | 回退 | 说明 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    strict_rows = {row["id"]: row for row in results["improved"]}
    auto_rows = {row["id"]: row for row in results["auto_with_variants"]}
    for row in results["improved"]:
        if row["set"] != case_set:
            continue
        strict = strict_rows[row["id"]]
        auto = auto_rows[row["id"]]
        variants = ", ".join(row["query_variants"]) or "—"
        lines.append(
            f"| {row['id']} | {row['question']} | {row['query']}（{variants}） | "
            f"{strict['first_result'] or '（空）'} | {auto['first_result'] or '（空）'} | "
            f"{percent(strict['recall_at_k'])} | {percent(auto['recall_at_k'])} | "
            f"{'是' if auto['fallback_applied'] else '否'} | {row.get('notes') or ''} |"
        )
    lines.append("")
    return lines


def rebuild_cases(results: dict, case_set: str) -> list[str]:
    lines = [
        f"### 无答案查询是否被误召回（{case_set}）",
        "",
        "| 用例 | 问题 | 严格返回 | auto 返回 |",
        "| --- | --- | --- | --- |",
    ]
    strict_rows = {row["id"]: row for row in results["improved"]}
    auto_rows = {row["id"]: row for row in results["auto_with_variants"]}
    for row in results["improved"]:
        if row["set"] != case_set or row["relevant"]:
            continue
        strict = strict_rows[row["id"]]
        auto = auto_rows[row["id"]]
        lines.append(
            f"| {row['id']} | {row['question']} | {len(strict['top'])} 条 | {len(auto['top'])} 条 |"
        )
    lines.append("")
    return lines


def compact_section(store, selection, budget: int) -> list[str]:
    """Compact versus full character cost, measured on the synthetic fixture only."""
    full_all = store.context(selection, 100_000, "full")
    compact_all = store.context(selection, 100_000, "compact")
    full = store.context(selection, budget, "full")
    compact = store.context(selection, budget, "compact")
    per_full = (full_all["memory_json_chars"] - 2) / max(len(full_all["memories"]), 1)
    per_compact = (compact_all["memory_json_chars"] - 2) / max(len(compact_all["memories"]), 1)
    saving = 1 - per_compact / per_full if per_full else 0.0
    return [
        "## compact 与 full 的成本对比（合成夹具，只读）",
        "",
        f"同一批记录、同一 max_chars={budget}，只改变 view 参数；未访问正式库。",
        "",
        "| 指标 | full | compact |",
        "| --- | --- | --- |",
        f"| 返回记录数 | {len(full['memories'])} | {len(compact['memories'])} |",
        f"| memories 数组字符数 | {full['memory_json_chars']} | {compact['memory_json_chars']} |",
        f"| 平均每条字符数 | {per_full:.1f} | {per_compact:.1f} |",
        f"| 本页遗漏条数 | {full['omitted_from_page']} | {compact['omitted_from_page']} |",
        "",
        (
            f"平均每条变化 **{saving * 100:+.1f}%**（正数表示 compact 更省）。compact 的键集合、"
            "每条提示和 null 占位是固定开销，所以来源字段稀疏时每条反而更大；来源字段冗长"
            "（长 evidence、多 files、白名单外的 quote 等）时 compact 明显更小。是否值得用 compact "
            "取决于数据的来源密度，不是普遍结论。正文不截断，verification 与 epistemic_status 完整保留。"
        ),
        "",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "v0.3-report.md")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    fixture = load_fixture()
    fixture_db = ROOT / "outputs" / "eval-fixture.sqlite3"
    if fixture_db.exists():
        fixture_db.unlink()
    store = build_store(fixture_db, fixture)
    results = evaluate(store, fixture)

    tuning = sum(1 for case in fixture["cases"] if case["set"] == "tuning")
    development = sum(1 for case in fixture["cases"] if case["set"] == "development")
    validation = sum(1 for case in fixture["cases"] if case["set"] == "validation")
    lines = [
        "# Personal Memory MCP v0.3 检索验收报告",
        "",
        f"生成时间：{datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
        (
            f"合成记忆 {len(fixture['memories'])} 条，用例 {len(fixture['cases'])} 条"
            f"（tuning {tuning}、development {development}、validation {validation}），K=5。"
            "数据全部为虚构样例，未使用正式库。"
        ),
        "",
        "三个集合的口径必须分清：",
        "",
        "- **tuning**：v0.2 的 20 条用例，`query_variants` 与 RRF 是照着它们设计的。",
        (
            "- **development**：14 条用例曾标为 acceptance，其中 A01 直接促成了 unusable_fragments 的实现，"
            "因此它们参与过实现定型，**不是**留出集，报告不使用“未用于调参/held-out”这类说法。"
        ),
        (
            "- **validation**：宽松回退冻结之后新写的 16 条（10 条长自然语言问句 + 6 条无答案负例）。"
            "开发者会看到结果，因此只能称为 validation，**不是 blind held-out**。"
        ),
        "",
        (
            f"宽松回退阈值：每路候选上限 {FALLBACK_CANDIDATES}，"
            f"通用片段判定 DF≥{GENERIC_DF_MIN} 且占比≥{GENERIC_DF_RATIO}，"
            f"覆盖要求 {MIN_COVERAGE_FRAGMENTS} 个不同片段；片段先算 DF 再按区分度取前 "
            f"{FALLBACK_MAX_FRAGMENTS} 个。"
        ),
        "",
        "## 汇总",
        "",
    ]
    lines += summary_table(results, "validation", "冻结后验证集 validation（非 blind held-out）")
    lines += summary_table(results, "development", "开发集 development（参与过实现定型）")
    lines += summary_table(results, "tuning", "调参集 tuning（v0.2 用例）")
    lines += ["## 明细", ""]
    lines += before_after_table(results, "validation")
    lines += rebuild_cases(results, "validation")
    lines += before_after_table(results, "development")
    lines += rebuild_cases(results, "development")
    lines += before_after_table(results, "tuning")
    lines += compact_section(store, Search(scope="project", scope_id="project:personal-memory", include_global=True, limit=20), 6000)
    lines += [
        "## 局限",
        "",
        "- 全部为词法匹配：宽松回退用 OR 召回候选并按片段覆盖筛选，仍然不是语义检索。",
        "- 严格模式仍是默认值；auto 只改变候选池为空时的行为，不改变已有命中。",
        "- 分页越界、范围为空都不触发回退，避免把分页问题伪装成关键词问题。",
        "- 无答案用例的“首条正确”定义为返回空；这不是“永不返回空”的验收目标。",
        (
            "- **已知误召回**：validation 的 V16（问“投资账户密码”）只凑出 1 个可用片段（投资），"
            "覆盖要求因此降为 1，于是返回了投资偏好记录，误召回率 1/6 = 16.7%。在正式库上复核发现"
            "这不限于 `coverage_required == 1`：证件、宠物类问题只是命中了“之前”“还记/记得”这类口语"
            "套话，`coverage_required` 仍为 2——基于 DF 的通用词过滤在几十条记录的小型个人库里不稳定。"
            "因此 auto 的定位是候选发现：所有 relaxed 结果都必须做语义核对，`coverage_required == 1` "
            "只是最低的一档而不是唯一的低可信度信号。用例保留在集合中而不删除。"
        ),
        "- 片段选择按 DF 从低到高、同 DF 保持原序；因此当一个长问句里所有片段 DF 相同（例如所有词都同样常见）时，仍会按位置取前若干个。",
        "- 关键词仍由客户端提取；后端用例通过不代表客户端一定改写正确。",
        "",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "report": str(args.out),
                **{
                    case_set: {
                        key: summarize_by_set(results[key], case_set) for key, _ in CONFIGURATIONS
                    }
                    for case_set in ("validation", "development", "tuning")
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
