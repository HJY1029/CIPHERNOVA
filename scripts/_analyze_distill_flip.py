#!/usr/bin/env python3
"""List Qwen cells: baseline (no distill) fail -> distill pass."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "experiments" / "results"
DB = ROOT / "code_history.db"


def cell_label(cell: dict) -> str:
    a = cell.get("algorithm", "")
    m = cell.get("mode") or ""
    l = cell.get("language", "")
    parts = [a, m, l] if m else [a, l]
    return "-".join(str(x) for x in parts if x)


def cell_key(algorithm: str, mode: str | None, language: str) -> str:
    return cell_label({"algorithm": algorithm, "mode": mode, "language": language})


def analyze_ckpt(path: Path) -> list[dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    prov = (d.get("fp_payload") or {}).get("provider", "?")
    base = d.get("results_baseline") or []
    dist = d.get("results_distill") or []
    out = []
    for i, b in enumerate(base):
        t = dist[i] if i < len(dist) else {}
        cell = b.get("cell") or t.get("cell") or {}
        fb, fd = bool(b.get("ftpr")), bool(t.get("ftpr"))
        if (not fb) and fd:
            out.append(
                {
                    "ckpt": path.name,
                    "provider": prov,
                    "label": cell_label(cell),
                    "baseline_history_skip": b.get("history_skip"),
                    "distill_history_skip": t.get("history_skip"),
                }
            )
    return out


def baseline_failures() -> list[dict]:
    out = []
    for p in sorted(RESULTS.glob("distill_*_ckpt.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        prov = (d.get("fp_payload") or {}).get("provider", "?")
        for b in d.get("results_baseline") or []:
            if b.get("ftpr"):
                continue
            cell = b.get("cell") or {}
            out.append(
                {
                    "ckpt": p.name,
                    "provider": prov,
                    "label": cell_label(cell),
                    "error": (b.get("error") or b.get("vector_detail") or "")[:100],
                    "distill_done": len(d.get("results_distill") or []) > 0,
                }
            )
    return out


def history_pass_with_distill(labels: set[str]) -> dict[str, dict]:
    """Slots in ``labels`` that have test_success=1 and distillation_active=True in DB."""
    if not DB.exists():
        return {}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    hits: dict[str, dict] = {}
    for r in con.execute(
        """
        SELECT provider, algorithm, mode, language, test_success, extra_data, created_at
        FROM code_history
        WHERE (provider LIKE '%qwen%' OR provider LIKE '%local%')
          AND test_success = 1
        """
    ):
        extra = json.loads(r["extra_data"] or "{}")
        if extra.get("distillation_active") is not True:
            continue
        ck = cell_key(r["algorithm"] or "", r["mode"], r["language"] or "")
        if ck in labels and ck not in hits:
            hits[ck] = {
                "provider": r["provider"],
                "created_at": r["created_at"],
            }
    con.close()
    return hits


def main() -> None:
    rows = []
    for p in sorted(RESULTS.glob("distill_*_ckpt.json")):
        rows.extend(analyze_ckpt(p))

    llm_flip = [
        r
        for r in rows
        if not r.get("baseline_history_skip") and not r.get("distill_history_skip")
    ]

    print("=== 正式消融：无蒸馏未过 -> 有蒸馏通过 ===")
    print(f"翻转格数: {len(rows)}（两阶段均调 LLM: {len(llm_flip)}）")
    if rows:
        for r in rows:
            print(f"  {r['label']:28}  ({r['ckpt']})")
    else:
        print("  （无：多数 ckpt 的 results_distill 仍为空）")
    print()

    bf = baseline_failures()
    labels = {x["label"] for x in bf}
    hist = history_pass_with_distill(labels)

    print("=== 无蒸馏基线失败、库中曾有「开蒸馏且测试通过」同槽 ===")
    print(f"基线失败 {len(bf)} 格；其中历史开蒸馏通过 {len(hist)} 格")
    for x in bf:
        ck = x["label"]
        h = hist.get(ck)
        mark = "=> 开蒸馏后曾通过" if h else "=> 尚无开蒸馏通过记录"
        print(f"  {ck:28}  {mark}")
        if not x["distill_done"]:
            print(f"    （消融有蒸馏阶段未跑完）")
    print()

    if bf:
        print("=== 无蒸馏基线失败明细（待跑有蒸馏阶段验证）===")
        for x in bf:
            print(f"  {x['label']:28}  {x['error']}")

    # Cross: ablation baseline fail vs earlier/later DB pass with distill (日常批量默认开蒸馏)
    print()
    print("=== 推断「关蒸馏消融失败、日常开蒸馏曾通过」（非同一 run 配对）===")
    for x in bf:
        h = hist.get(x["label"])
        if h:
            print(f"  {x['label']:28}  DB通过 @{h['created_at']}  ({h['provider']})")


if __name__ == "__main__":
    main()
