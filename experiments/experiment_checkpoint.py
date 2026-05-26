"""实验断点续跑：原子写入 JSON 检查点，便于关机后从已完成任务继续。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


def fingerprint_from_payload(payload: Dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:24]


def atomic_write_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_json_optional(path: Path) -> Optional[Dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def checkpoint_mismatch_message(path: Path, reason: str) -> str:
    return (
        f"检查点与当前参数不一致（{reason}），无法 --resume。\n"
        f"  文件: {path}\n"
        "  请删除该检查点文件后重新全量运行，或改用与上次相同的网格/配置。"
    )
