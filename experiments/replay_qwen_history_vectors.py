#!/usr/bin/env python3
"""
对 ``code_history.db`` 中 Qwen 线路历史代码执行与 Web 批量相同的 ``rerun_vector_tests_on_code`` 复测。

分类：
- **无测试数据**：复测返回 (None, None)（库中无 golden / tester 不可用）
- **未通过**：返回了测试结果但首元为失败（含标准向量失败，或启用 OpenSSL 官方向量链时其后段失败）

用法（项目根目录）::

  python experiments/replay_qwen_history_vectors.py
  python experiments/replay_qwen_history_vectors.py --latest-per-slot
  python experiments/replay_qwen_history_vectors.py --limit 10
  python experiments/replay_qwen_history_vectors.py -o experiments/results/qwen_retest.txt
  python experiments/replay_qwen_history_vectors.py --purge --all-providers
  python experiments/replay_qwen_history_vectors.py --purge --dry-run --all-providers
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.code_saver import rerun_vector_tests_on_code  # noqa: E402
from agent.crypto_agent import CryptoAgent  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402


def _fetch_rows(
    db_path: Path,
    *,
    latest_per_slot: bool,
    only_success: bool,
    limit: Optional[int],
    all_providers: bool,
) -> List[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    succ_clause = " AND COALESCE(test_success, 0) = 1" if only_success else ""
    prov_clause = (
        ""
        if all_providers
        else " AND instr(lower(trim(COALESCE(provider, ''))), 'qwen') > 0"
    )
    lim_clause = f" LIMIT {int(limit)}" if limit is not None and limit > 0 else ""

    if latest_per_slot:
        sql = f"""
        SELECT h.id, h.timestamp, h.provider, h.algorithm, h.mode, h.language,
               h.operation, h.test_success, h.filename
        FROM code_history h
        INNER JOIN (
            SELECT algorithm,
                   mode,
                   language,
                   MAX(id) AS mid
            FROM code_history
            WHERE 1=1
              {prov_clause}
              {succ_clause}
            GROUP BY algorithm, mode, language
        ) t ON h.id = t.mid
        WHERE 1=1
          {prov_clause}
        ORDER BY h.id
        """ + lim_clause
        cur = conn.execute(sql)
    else:
        sql = f"""
        SELECT id, timestamp, provider, algorithm, mode, language,
               operation, test_success, filename
        FROM code_history
        WHERE 1=1
          {prov_clause}
          {succ_clause}
        ORDER BY id DESC
        """ + lim_clause
        cur = conn.execute(sql)
    rows = cur.fetchall()
    conn.close()

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    out: List[sqlite3.Row] = []
    for r in rows:
        rid = r["id"]
        one = conn2.execute(
            "SELECT id, timestamp, provider, algorithm, mode, language, "
            "operation, test_success, filename, code FROM code_history WHERE id = ?",
            (rid,),
        ).fetchone()
        if one:
            out.append(one)
    conn2.close()
    return list(reversed(out)) if not latest_per_slot else out


def _label(
    tr: Optional[Tuple[bool, str, Dict[str, Any]]], otr: Any
) -> Tuple[str, str]:
    if tr is None:
        return "no_test_data", "无标准测试数据或无法复测（返回 None）"
    ok = tr[0]
    msg = tr[1] if len(tr) > 1 else ""
    if ok:
        return "pass", "通过"
    m = str(msg)[:400]
    if otr is not None and len(otr) > 1 and not otr[0]:
        return "fail", f"未通过（含 OpenSSL 链/对照；说明: {m}）"
    return "fail", f"未通过: {m}"


async def _run(rows: List[sqlite3.Row], provider: str, config_path: Path) -> List[Dict[str, Any]]:
    agent = CryptoAgent(
        config_path=str(config_path),
        provider=provider,
        enable_validation=False,
    )
    sem_n = 1
    try:
        sem_n = max(1, int(agent.config.get("local_batch_concurrency", 1) or 1))
    except Exception:
        sem_n = 1
    sem = asyncio.Semaphore(sem_n)

    async def one(row: sqlite3.Row) -> Dict[str, Any]:
        async with sem:
            code = row["code"] or ""
            op = (row["operation"] or "").strip() or "加密解密"
            mode = row["mode"]
            tr, otr = await rerun_vector_tests_on_code(
                agent,
                code,
                row["algorithm"],
                mode,
                row["language"],
                operation=str(op),
            )
            cat, detail = _label(tr, otr)
            return {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "provider": row["provider"],
                "algorithm": row["algorithm"],
                "mode": mode,
                "language": row["language"],
                "operation": op,
                "db_test_success": row["test_success"],
                "filename": row["filename"],
                "category": cat,
                "detail": detail,
            }

    return await asyncio.gather(*[one(r) for r in rows])


def main() -> int:
    ap = argparse.ArgumentParser(description="复测 code_history 中 Qwen 历史代码的标准向量路径")
    ap.add_argument("--db", type=Path, default=ROOT / "code_history.db")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument(
        "--provider",
        default="qwen_coder_local",
        help="用于构造 CryptoAgent（须能加载 tester / 测试数据）",
    )
    ap.add_argument(
        "--latest-per-slot",
        action="store_true",
        help="每 (算法,模式,语言) 只复测 id 最新一条；否则按时间倒序遍历所有命中行",
    )
    ap.add_argument(
        "--include-fail-rows",
        action="store_true",
        help="默认只复测库中 test_success=1 的行；加本项则包含失败/空行",
    )
    ap.add_argument("--limit", type=int, default=0, help="最多复测多少条（0 表示不限制）")
    ap.add_argument(
        "--all-providers",
        action="store_true",
        help="不限于 provider 名含 qwen 的行（--purge 时建议开启）",
    )
    ap.add_argument(
        "--purge",
        action="store_true",
        help="删除库中 test_success=1 但标准向量复测未通过（或无测试数据）的记录",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="与 --purge 联用：只列出将删除的 id，不写库",
    )
    ap.add_argument("-o", "--output", type=Path, default=None, help="写入 UTF-8 文本报告")
    args = ap.parse_args()

    db_path = args.db if args.db.is_absolute() else ROOT / args.db
    if not db_path.is_file():
        print(f"未找到数据库: {db_path}", file=sys.stderr)
        return 2

    limit = args.limit if args.limit > 0 else None
    only_success = not args.include_fail_rows
    if args.purge:
        only_success = True
    rows = _fetch_rows(
        db_path,
        latest_per_slot=args.latest_per_slot,
        only_success=only_success,
        limit=limit,
        all_providers=args.all_providers or args.purge,
    )
    if not rows:
        print("没有符合条件的记录。", file=sys.stderr)
        return 0

    cfg_path = args.config if args.config.is_absolute() else ROOT / args.config
    results = asyncio.run(_run(rows, args.provider, cfg_path))

    lines = [
        f"# Qwen 历史复测 {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"# db={db_path}",
        f"# rows={len(results)} latest_per_slot={args.latest_per_slot} only_success={not args.include_fail_rows}",
        "",
    ]
    bad = [r for r in results if r["category"] != "pass"]
    summary = {}
    for r in results:
        summary[r["category"]] = summary.get(r["category"], 0) + 1
    lines.append("## 汇总")
    lines.append(f"- pass: {summary.get('pass', 0)}")
    lines.append(f"- fail: {summary.get('fail', 0)}")
    lines.append(f"- no_test_data: {summary.get('no_test_data', 0)}")
    lines.append("")
    lines.append("## 未通过或无测试数据")
    lines.append("| id | algorithm | mode | lang | category | detail |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in bad:
        mode = r["mode"] or "—"
        det = str(r["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r['id']} | {r['algorithm']} | {mode} | {r['language']} | {r['category']} | {det} |"
        )

    to_purge = [
        r
        for r in results
        if int(r.get("db_test_success") or 0) == 1 and r["category"] != "pass"
    ]
    if args.purge:
        lines.append("")
        lines.append("## 误判成功（将删除）" if not args.dry_run else "## 误判成功（dry-run，未删除）")
        lines.append(f"- 条数: **{len(to_purge)}**")
        if to_purge and not args.dry_run:
            hm = HistoryManager(db_path=str(db_path))
            n_del = hm.delete_history_by_ids([int(r["id"]) for r in to_purge])
            lines.append(f"- 已从 `code_history.db` 删除: **{n_del}** 条")
            print(f"已删除误判成功记录 {n_del} 条。", file=sys.stderr)
        elif to_purge and args.dry_run:
            print(f"[dry-run] 将删除 {len(to_purge)} 条误判成功记录。", file=sys.stderr)

    text = "\n".join(lines) + "\n"

    print(text)
    if args.output:
        out = args.output if args.output.is_absolute() else ROOT / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"已写入: {out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
