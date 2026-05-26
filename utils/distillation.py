"""
本地模型提示蒸馏：JSONL 教师池检索与注入（与 paperzh 七轴框架对齐的推断侧操作化）。

JSONL 每行示例字段（尽量兼容，缺失则回退默认）：
- algorithm, mode, language: 任务匹配（RSA 时用 operation 代替 mode，见 mode_or_op）
- operation: RSA 等任务子类型
- code: 教师通过代码（必填）
- quality_score: float 0~1，默认 1.0
- provider: 教师来源，用于静态/动态加权
- impl_tag: 如 openssl_only / pure_impl，用于双教师差异化
- passed: bool，true 表示正例（默认 true）
- failed_code: 失败学生代码，作 hard negative（C 轴）
- error_cluster: 错误簇标签（B 轴检索）
- process_note 或 fix_trajectory: 过程蒸馏短文（A 轴）
- ts / recorded_at: ISO 时间戳，用于新鲜度衰减（F 轴）
"""
from __future__ import annotations

import json
import math
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import setup_logger

logger = setup_logger()

_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

ABLATION_FLAGS = {
    "baseline": {"error_key": False, "hard_neg": False, "process": False, "dual": False},
    "error_conditioned": {"error_key": True, "hard_neg": False, "process": False, "dual": False},
    "hard_negative": {"error_key": False, "hard_neg": True, "process": False, "dual": False},
    "process_trajectory": {"error_key": False, "hard_neg": False, "process": True, "dual": False},
    "full": {"error_key": True, "hard_neg": True, "process": True, "dual": True},
}


def _cfg(agent: Any) -> Dict[str, Any]:
    raw = getattr(agent, "config", None)
    if raw is None:
        return {}
    return raw.get("distillation") or {}


def distillation_enabled(agent: Any) -> bool:
    c = _cfg(agent)
    return bool(c.get("enabled")) and bool(c.get("dataset_path"))


def is_distillation_target_provider(agent: Any) -> bool:
    if not distillation_enabled(agent):
        return False
    c = _cfg(agent)
    names = c.get("local_providers")
    p = (getattr(agent, "provider", "") or "").lower()
    if isinstance(names, list) and names:
        return p in {str(x).lower() for x in names}
    return "local" in p


def _norm_lang(lang: str) -> str:
    x = (lang or "python").lower()
    return "cpp" if x in ("c++", "cpp") else x


def _task_mode(algorithm: str, mode: Optional[str], operation: Optional[str]) -> str:
    if algorithm and algorithm.upper() == "RSA":
        return (operation or "").strip()
    return (mode or "").strip()


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    key = str(p.resolve())
    hit = _CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    rows: List[Dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    _CACHE[key] = (mtime, rows)
    logger.info(f"蒸馏数据集已加载: {key} ({len(rows)} 条)")
    return rows


def _whitelist_ok(code: str, patterns: List[str]) -> bool:
    if not patterns:
        return True
    for pat in patterns:
        try:
            if re.search(pat, code, re.DOTALL):
                return True
        except re.error:
            continue
    return False


def _blacklist_ok(code: str, patterns: List[str]) -> bool:
    for pat in patterns:
        try:
            if re.search(pat, code, re.DOTALL):
                return False
        except re.error:
            continue
    return True


def _record_quality(r: Dict[str, Any], default: float = 1.0) -> float:
    try:
        q = float(r.get("quality_score", default))
        return max(0.0, min(1.0, q))
    except (TypeError, ValueError):
        return default


def _teacher_code_char_limit(cfg: Dict[str, Any]) -> Optional[int]:
    """正整数：截断注入的教师代码长度，减轻本地 7B 上下文压力；缺省或 ≤0 表示不截断。"""
    v = cfg.get("max_teacher_code_chars")
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _maybe_truncate_teacher_code(code: str, limit: Optional[int]) -> str:
    if not code or not limit or len(code) <= limit:
        return code
    marker = (
        "\n\n# ... [蒸馏截断：仅保留前段；请学 env 读取、模式常量与 print 格式，勿整段照抄长表] ...\n"
    )
    avail = limit - len(marker)
    if avail < 120:
        return code[:limit].rstrip()
    return code[:avail].rstrip() + marker


def _infer_error_cluster(test_feedback: Optional[Dict[str, Any]], message: str = "") -> str:
    msg = (message or "").lower()
    if test_feedback:
        o = str(
            test_feedback.get("output", "")
            or test_feedback.get("actual", "")
            or test_feedback.get("message", "")
            or ""
        ).lower()
        msg += " " + o
    # 先于泛化「error」匹配：避免 TypeError 等被误归为 compile_or_syntax
    if (
        "unexpected keyword argument" in msg
        or "suppress_heuristic_warnings" in msg
        or ("save_code" in msg and "unexpected" in msg)
    ):
        return "agent_infrastructure"
    # OpenSSL 开发头缺失 / 预处理失败：稳定归入编译簇，确保蒸馏注入「编译/语法簇」段落（含 evp.h 专项修复提示）
    if (
        "openssl/evp.h" in msg
        or "openssl/des.h" in msg
        or "openssl/aes.h" in msg
        or "openssl/ssl.h" in msg
        or "openssl/err.h" in msg
        or ("no such file or directory" in msg and "openssl" in msg)
        or ("fatal error" in msg and "openssl" in msg)
    ):
        return "compile_or_syntax"
    if "undefined reference" in msg and "remove_whitespace" in msg:
        return "missing_static_helper"
    if "undefined reference" in msg and "_start" in msg and "main" in msg:
        return "missing_main_entry"
    if ("'the' has not been declared" in msg) or ("`the' has not been declared" in msg):
        return "cpp_typo_the_keyword"
    if "error" in msg or "编译" in msg or "compile" in msg or "syntax" in msg:
        return "compile_or_syntax"
    exp = str(test_feedback.get("expected", "") if test_feedback else "")
    act = str(test_feedback.get("actual", "") if test_feedback else "")
    if exp and act and len(exp) != len(act):
        return "length_mismatch"
    if "iv" in msg or "08080808" in msg:
        return "iv_or_padding_output"
    if "plaintext" in msg or "明文" in msg:
        return "wrong_plaintext_as_cipher"
    return "generic_test_fail"


def _provider_dyn_weights(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    by_p: Dict[str, List[float]] = {}
    for r in rows:
        p = str(r.get("provider") or "unknown").lower()
        by_p.setdefault(p, []).append(_record_quality(r))
    out: Dict[str, float] = {}
    for p, scores in by_p.items():
        n = len(scores)
        mean_q = sum(scores) / n if n else 0.0
        # 轻量动态因子：质量均值 × log(1+n) 归一化
        out[p] = (0.5 + mean_q) * math.log1p(n)
    m = max(out.values()) if out else 1.0
    if m <= 0:
        m = 1.0
    return {k: v / m for k, v in out.items()}


def _freshness(r: Dict[str, Any], half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    ts = r.get("ts") or r.get("recorded_at")
    if not ts:
        return 1.0
    try:
        # 仅支持 Unix 数字或 ISO 粗略解析
        if isinstance(ts, (int, float)):
            t = float(ts)
        else:
            from datetime import datetime

            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 1.0
    age_days = max(0.0, (time.time() - t) / 86400.0)
    return math.exp(-math.log(2) * age_days / half_life_days)


def _weight_row(
    r: Dict[str, Any],
    static_w: Dict[str, float],
    dyn: Dict[str, float],
    half_life_days: float,
) -> float:
    p = str(r.get("provider") or "unknown").lower()
    ws = float(static_w.get(p, static_w.get("default", 1.0)))
    wd = float(dyn.get(p, 1.0))
    q = _record_quality(r)
    return ws * wd * q * _freshness(r, half_life_days)


def _match_task(r: Dict[str, Any], algorithm: str, mode_or_op: str, language: str) -> bool:
    if str(r.get("algorithm", "")).upper() != (algorithm or "").upper():
        return False
    if _norm_lang(str(r.get("language", ""))) != _norm_lang(language):
        return False
    if (algorithm or "").upper() == "RSA":
        if (r.get("operation") or "").strip() != (mode_or_op or "").strip():
            return False
    else:
        if str(r.get("mode", "")).upper() != str(mode_or_op or "").upper():
            return False
    return True


def _filter_pool(
    rows: List[Dict[str, Any]],
    algorithm: str,
    mode_or_op: str,
    language: str,
    cfg: Dict[str, Any],
    flags: Dict[str, bool],
    error_cluster: Optional[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (正例池, hard negative 池)。"""
    min_q = float(cfg.get("min_quality_score_for_retrieval", 0.0))
    wl = cfg.get("whitelist_patterns") or []
    if isinstance(wl, str):
        wl = [wl]
    bl = cfg.get("blacklist_patterns") or []
    if isinstance(bl, str):
        bl = [bl]
    positives: List[Dict[str, Any]] = []
    negatives: List[Dict[str, Any]] = []
    for r in rows:
        if not _match_task(r, algorithm, mode_or_op, language):
            continue
        code = r.get("code") or ""
        if not isinstance(code, str) or len(code.strip()) < 20:
            continue
        if _record_quality(r) < min_q:
            continue
        if not _whitelist_ok(code, list(wl)):
            continue
        if not _blacklist_ok(code, list(bl)):
            continue
        passed = r.get("passed", True)
        if passed is False:
            fc = r.get("failed_code")
            if isinstance(fc, str) and len(fc.strip()) > 20:
                negatives.append(r)
            continue
        if flags["error_key"] and error_cluster:
            ec = r.get("error_cluster")
            if ec is not None and str(ec).strip() != "" and str(ec) != str(error_cluster):
                continue
        positives.append(r)
    if not positives and flags["error_key"] and error_cluster:
        # 回退：不用错误簇过滤
        return _filter_pool(rows, algorithm, mode_or_op, language, cfg, {**flags, "error_key": False}, None)
    return positives, negatives


def _weighted_sample_without_replacement(
    items: List[Dict[str, Any]], weights: List[float], k: int, rng: random.Random
) -> List[Dict[str, Any]]:
    pool = list(zip(items, weights))
    out: List[Dict[str, Any]] = []
    for _ in range(min(k, len(pool))):
        if not pool:
            break
        ws = [max(0.0, w) for _, w in pool]
        s = sum(ws)
        if s <= 0:
            break
        pick = rng.uniform(0, s)
        acc = 0.0
        idx = 0
        for i, w in enumerate(ws):
            acc += w
            if acc >= pick:
                idx = i
                break
        out.append(pool[idx][0])
        pool.pop(idx)
    return out


def _pick_dual_teachers(
    candidates: List[Dict[str, Any]], weights: List[float], rng: random.Random
) -> List[Dict[str, Any]]:
    if len(candidates) < 2:
        return _weighted_sample_without_replacement(candidates, weights, 1, rng)
    # 优先 provider 或 impl_tag 不同
    first = _weighted_sample_without_replacement(candidates, weights, 1, rng)
    if not first:
        return []
    f = first[0]
    p0 = str(f.get("provider", "")).lower()
    t0 = str(f.get("impl_tag", "")).lower()
    rest_idx = [
        i
        for i, c in enumerate(candidates)
        if str(c.get("provider", "")).lower() != p0 or str(c.get("impl_tag", "")).lower() != t0
    ]
    if rest_idx:
        rest = [candidates[i] for i in rest_idx]
        w2 = [weights[i] for i in rest_idx]
        second = _weighted_sample_without_replacement(rest, w2, 1, rng)
        return [f] + second
    return _weighted_sample_without_replacement(candidates, weights, 2, rng)


def build_few_shot_prefix(
    agent: Any,
    algorithm: str,
    mode: Optional[str],
    operation: Optional[str],
    language: str,
) -> str:
    if not is_distillation_target_provider(agent):
        return ""
    cfg = _cfg(agent)
    path = cfg.get("dataset_path") or ""
    n = int(cfg.get("max_few_shot_examples", 2))
    if n <= 0:
        return ""
    mode_ab = str(cfg.get("ablation_mode", "full")).lower()
    flags = ABLATION_FLAGS.get(mode_ab, ABLATION_FLAGS["full"])
    rows = _load_jsonl(path)
    if not rows:
        logger.warning(
            "蒸馏已启用且当前为本地目标 provider，但教师池 JSONL 为空或不存在（路径: %s）。"
            "首轮不会注入少样本；请先填充该文件或开启云端自动收集（config: auto_collect_cloud_teachers）。",
            path,
        )
        return ""
    mode_or_op = _task_mode(algorithm, mode, operation)
    positives, _ = _filter_pool(rows, algorithm, mode_or_op, language, cfg, flags, None)
    if not positives:
        logger.warning(
            "蒸馏教师池共 %d 条，但无与 algorithm=%s mode/op=%s language=%s 匹配的正例，"
            "首轮未注入少样本（重试轮次若已有新条目则可能注入）。",
            len(rows),
            algorithm,
            mode_or_op,
            language,
        )
        return ""
    static = cfg.get("static_provider_weights") or {}
    if not isinstance(static, dict):
        static = {}
    dyn = _provider_dyn_weights(rows)
    half = float(cfg.get("freshness_half_life_days", 0) or 0)
    rng = random.Random((hash(path) ^ hash(algorithm) ^ hash(mode_or_op) ^ hash(language)) & 0xFFFFFFFF)
    ws = [_weight_row(r, static, dyn, half) for r in positives]
    picks = _weighted_sample_without_replacement(positives, ws, n, rng)
    tlim = _teacher_code_char_limit(cfg)
    parts = [
        "**【知识蒸馏 · 首轮少样本】** 以下为已通过标准测试的教师实现片段，请参考其结构与 I/O 约定，"
        "仍需独立写出完整源码，禁止逐字抄袭无关部分。"
        "**核对：** 教师代码的 **算法/模式须与本题一致**（例：DES-CBC 不得抄 DES-CFB；`EVP_des_cbc` ≠ `EVP_des_cfb8`）；输出须含 **`密文:`**。"
        "**环境：** 教师可能含 **`#include <openssl/evp.h>`**；若你目标机**无该头文件**（预处理失败），须**保留算法与格式、改为不依赖 `<openssl/` 的单文件实现**，勿照搬会导致 `fatal error: openssl/evp.h` 的 include。"
    ]
    if tlim:
        parts.append(
            f"**【小上下文】** 下列示例若经截断，只学 `import`、环境变量与密文输出格式，勿复制大段常量表。"
        )
    for i, r in enumerate(picks, 1):
        code = _maybe_truncate_teacher_code((r.get("code") or "").strip(), tlim)
        tag = r.get("impl_tag") or r.get("provider") or "teacher"
        parts.append(f"--- 教师示例 {i}（{tag}）---\n{code}")
    return "\n\n".join(parts)


def build_improve_suffix(
    agent: Any,
    algorithm: str,
    mode: Optional[str],
    operation: Optional[str],
    language: str,
    test_feedback: Optional[Dict[str, Any]],
    student_code: str,
) -> str:
    if not is_distillation_target_provider(agent):
        return ""
    cfg = _cfg(agent)
    path = cfg.get("dataset_path") or ""
    max_refs = int(cfg.get("max_teacher_refs_improve", 1))
    max_refs = max(1, min(2, max_refs))
    mode_ab = str(cfg.get("ablation_mode", "full")).lower()
    flags = ABLATION_FLAGS.get(mode_ab, ABLATION_FLAGS["full"])
    rows = _load_jsonl(path)
    if not rows:
        return ""
    mode_or_op = _task_mode(algorithm, mode, operation)
    err = _infer_error_cluster(test_feedback, str(test_feedback.get("message", "") if test_feedback else ""))
    positives, negatives = _filter_pool(rows, algorithm, mode_or_op, language, cfg, flags, err)
    if not positives:
        positives, negatives = _filter_pool(
            rows, algorithm, mode_or_op, language, cfg, {**flags, "error_key": False}, None
        )
    if not positives:
        return ""
    static = cfg.get("static_provider_weights") or {}
    if not isinstance(static, dict):
        static = {}
    dyn = _provider_dyn_weights(rows)
    half = float(cfg.get("freshness_half_life_days", 0) or 0)
    rng = random.Random(
        (hash(path) ^ hash(student_code[:200]) ^ hash(str(test_feedback)) ) & 0xFFFFFFFF
    )
    ws = [_weight_row(r, static, dyn, half) for r in positives]
    if flags["dual"] and max_refs >= 2:
        teachers = _pick_dual_teachers(positives, ws, rng)
    else:
        teachers = _weighted_sample_without_replacement(positives, ws, 1, rng)
    blocks: List[str] = []
    blocks.append(
        "**【知识蒸馏 · 改进阶段教师参考】** 下列代码已通过同任务标准测试，请对照测试失败摘要修正你的实现。"
        " **注意：** 仅借鉴 **当前算法+模式** 的正确路径（勿把 AES 当 DES、勿把 CFB 当 OFB）。"
        " 若教师使用 **OpenSSL EVP** 而你的错误为 **`openssl/evp.h` 找不到**，须**改写为等价的纯标准库实现**，**不要**保留无法编译的 `#include <openssl/...>`。"
    )
    if flags["process"]:
        for t in teachers:
            note = t.get("process_note") or t.get("fix_trajectory")
            if isinstance(note, str) and note.strip():
                blocks.append(f"**过程提示（Outcome→Process）**：\n{note.strip()}")
                break
    tlim = _teacher_code_char_limit(cfg)
    hn_cap = tlim if tlim else 8000
    for i, t in enumerate(teachers, 1):
        code = _maybe_truncate_teacher_code((t.get("code") or "").strip(), tlim)
        prov = t.get("provider") or "teacher"
        blocks.append(f"--- 参考实现 {i}（{prov}）---\n{code}")
    if flags["hard_neg"] and negatives:
        nw = [_weight_row(r, static, dyn, half) for r in negatives]
        hn = _weighted_sample_without_replacement(negatives, nw, 1, rng)
        if hn:
            fc = (hn[0].get("failed_code") or "").strip()
            if fc:
                fc_show = fc[:hn_cap] if len(fc) > hn_cap else fc
                blocks.append(
                    "**【对比蒸馏 · Hard Negative】** 以下为未通过测试的写法，请避免同类错误，勿照搬：\n"
                    f"```\n{fc_show}\n```"
                )
    if err == "agent_infrastructure":
        blocks.append(
            "**【测试反馈说明】** 当前失败来自评测代理/保存代码接口与仓库版本不一致（如多余关键字参数），"
            "非密码学算法本身。请同步更新本仓库后重跑；改进阶段请勿为「适配」虚构不存在的 API 参数。"
        )
    if err == "missing_main_entry":
        blocks.append(
            "**【测试反馈说明】** 链接阶段 **`undefined reference to main`**：当前代码**没有可链接的 `main` 函数**"
            "（常见于只生成 S 盒/轮函数后截断）。须补全 **`int main`/`int main(void)`**：读 **`TEST_*`**、调用 **EVP 或完整加密流程**、打印 **`密文:`**。"
        )
    if err == "compile_or_syntax":
        blocks.append(
            "**【编译/语法簇 · 蒸馏】** 对照修复：① **`isspace`** ↔ **`#include <ctype.h>`** / **`<cctype>`**；"
            "② C++ **`malloc`→`static_cast<T*>`**；③ **`std::remove_if`** → **`<algorithm>`** 且调用 **`std::remove_if`**（勿裸 `remove_if`）；"
            "④ **`std::bitset`→`<bitset>`**，**`std::vector`→`<vector>`**；"
            "⑤ **`openssl/evp.h: No such file`** → **删除所有 OpenSSL include**，改手写或换到有 libssl-dev 的环境；"
            "⑥ **`conflicting types for 'ciphertext'`** → 密文缓冲区勿与 **`char *ciphertext`** 同名；"
            "⑦ **DES** 单密钥题 **`EVP_des_ecb`/`cbc`/`cfb8`/`ofb`** 与题面一致，**禁止** **`DES_ede3_*`** 或 CBC 题里写 CFB API。"
        )
    if flags["error_key"]:
        blocks.append(f"**【错误条件蒸馏】** 当前失败簇标签：`{err}`，请针对该簇根因修复。")
    blocks.append(
        "**【验证器引导 / 反事实核对】** 优先满足标准向量与输出格式；核对：模式与 **segment（CFB-8）**、**IV 是否误入输出**、"
        "**密文 hex 长度是否等于 2×明文字节**、**DES-OFB 是否误成 CFB**。"
        " 缺 OpenSSL 头时以**可 `gcc -lm` 预处理**为第一约束。"
    )
    return "\n\n".join(blocks)


def append_cloud_teacher_from_successful_run(
    agent: Any,
    *,
    algorithm: str,
    mode: Optional[str],
    operation: str,
    language: str,
    code: str,
) -> None:
    """云端等非本地 provider 跑通标准测试后，将代码追加写入 JSONL，供本地学生检索蒸馏。

    与 ``is_distillation_target_provider`` 相反：仅当当前 ``agent.provider`` **不是**
    ``local_providers`` 中的学生模型时才写入，避免本地自举污染教师池。
    """
    c = _cfg(agent)
    if not bool(c.get("enabled")):
        return
    if not bool(c.get("auto_collect_cloud_teachers", True)):
        return
    if is_distillation_target_provider(agent):
        return
    path = (c.get("dataset_path") or "").strip()
    if not path:
        return
    body = (code or "").strip()
    if len(body) < 20:
        return

    au = (algorithm or "").strip()
    lang = _norm_lang(language)
    rec: Dict[str, Any] = {
        "algorithm": au,
        "language": lang,
        "code": body,
        "provider": str(getattr(agent, "provider", "") or "unknown"),
        "passed": True,
        "quality_score": 1.0,
    }
    if au.upper() == "RSA":
        rec["operation"] = (operation or "").strip()
        rec["mode"] = ""
    else:
        rec["mode"] = (mode or "").strip()
        rec["operation"] = (operation or "加密解密").strip()

    try:
        from datetime import datetime, timezone

        rec["ts"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass

    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        logger.warning(f"蒸馏教师池目录不可写，跳过追加: {ex}")
        return

    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as ex:
        logger.warning(f"写入蒸馏教师池失败: {ex}")
        return

    key = str(p.resolve())
    _CACHE.pop(key, None)
    logger.info(f"蒸馏教师池已追加 1 条（provider={rec['provider']}, {au} {rec.get('mode') or rec.get('operation')})")
