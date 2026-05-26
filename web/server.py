"""
Web服务器 - 提供前端界面和API接口
支持多页面并发运行

多线程/多并发支持：
- 每个请求都会创建独立的Agent实例，支持真正的并发执行
- 多个页面可以同时使用相同的LLM进行代码生成
- 多个页面可以同时使用不同的LLM进行代码生成
- 使用FastAPI的异步特性实现真正的并发，不会相互阻塞
- 文件操作和任务管理使用线程锁确保线程安全
"""
import asyncio
import os
import threading
import time
import uuid
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from agent.crypto_agent import CryptoAgent
from agent.code_saver import rerun_vector_tests_on_code
from agent.llm_adapter import set_api_key_manager, LLMAdapter
from utils.config_loader import ConfigLoader
from utils.api_key_manager import APIKeyManager
from utils.logger import setup_logger
from utils.code_tester import CodeTester
from utils.code_validator import CodeValidator
from utils.openssl_tester import OpenSSLTester
from utils.test_data_loader import TestDataLoader
from utils.llm_provider_ui import llm_provider_display_name, llm_provider_key_ready
from utils.history_manager import HistoryManager
import yaml

logger = setup_logger()

# 批量 SSE「progress」帧勿附带完整源码：巨型 JSON 会令浏览器 JSON.parse 长时间阻塞主线程，
# 表现为终端已到下一项，网页进度文案仍停在上一条。
_BATCH_SSE_PROGRESS_INCLUDE_CODE = False

# 本地 Ollama 等单实例推理：批量若全开并发会排队至 httpx「Request timed out」
def _local_batch_concurrency() -> int:
    try:
        n = int(ConfigLoader().get("local_batch_concurrency", 1) or 1)
        return max(1, min(8, n))
    except Exception:
        return 1


_LOCAL_BATCH_SEM = asyncio.Semaphore(_local_batch_concurrency())


def _provider_is_local_batch(provider: str) -> bool:
    p = (provider or "").lower()
    return "local" in p or "ollama" in p


def _local_batch_skip_enabled() -> bool:
    try:
        v = ConfigLoader().get("local_batch_skip_enabled", True)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        return True


def _local_batch_skip_if_success_since() -> str:
    """config.yaml `local_batch_skip_if_success_since`：本地批量时若历史自该日(含)起已有同用例成功则跳过。"""
    try:
        v = ConfigLoader().get("local_batch_skip_if_success_since", "2026-05-04")
        if v is None:
            return "2026-05-04"
        s = str(v).strip()
        if not s or s.lower() in ("null", "none"):
            return "2026-05-04"
        return s[:10] if len(s) >= 10 else s
    except Exception:
        return "2026-05-04"


app = FastAPI(title="织密新星 (CipherNova)", version="2.0")

# 配置模板和静态文件
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 初始化API密钥管理器
api_key_manager = APIKeyManager()
# 设置到LLM适配器
set_api_key_manager(api_key_manager)

# 文件操作锁（确保文件操作的线程安全）
file_lock = threading.Lock()


def _batch_stream_label(config: Dict[str, Any]) -> str:
    alg = config.get("algorithm", "")
    mode = (config.get("mode") or "").strip()
    lang = config.get("language", "")
    if mode:
        return f"{alg} {mode} - {lang}"
    return f"{alg} - {lang}"


def _batch_case_id_from_config(config: Dict[str, Any]) -> str:
    alg = (config.get("algorithm") or "unk").strip().lower()
    lang = (config.get("language") or "unk").strip().lower()
    mode = config.get("mode")
    if mode is not None and str(mode).strip():
        m = str(mode).strip().lower().replace(" ", "-")
        return f"{alg}-{m}-{lang}"
    return f"{alg}-{lang}"


def _batch_vector_display(vector_status: str, vector_detail: str) -> str:
    vs = (vector_status or "").strip() or "—"
    vd = (vector_detail or "").strip()
    if vs == "跳过" and vd:
        return f"跳过 {vd}"
    return vs


def _batch_result_pack(
    config: Dict[str, Any],
    *,
    success: bool,
    generation_ok: bool,
    vector_status: str,
    vector_detail: str,
    error: Optional[str],
    code: Optional[str],
    filename: Optional[str],
    total_ms: int,
    generated_code: Optional[str] = None,
    generated_filename: Optional[str] = None,
) -> Dict[str, Any]:
    vd = vector_detail or ""
    # 未通过标准测试等情况下不向页面附带源码，避免「失败仍展示代码」
    gc = generated_code.strip() if isinstance(generated_code, str) else None
    if gc == "":
        gc = None
    gf = generated_filename
    if not success:
        code = None
        filename = None
    else:
        # 成功条目只用 code；避免与 generated_code 重复
        gc = None
        gf = None
    return {
        "config": config,
        "success": success,
        "error": error,
        "code": code,
        "filename": filename,
        "generation_ok": generation_ok,
        "vector_status": vector_status,
        "vector_detail": vd,
        "vector_display": _batch_vector_display(vector_status, vd),
        "total_ms": total_ms,
        "case_id": _batch_case_id_from_config(config),
        # 仅失败且已落盘时存在：供 batch 错误日志记录，前端/SSE 仍不展示 code 字段中的失败源码
        "generated_code": gc,
        "generated_filename": gf,
    }


async def _batch_generate_single(
    provider: str,
    config: Dict[str, Any],
    *,
    agent: Optional[CryptoAgent] = None,
    generate_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """单条批量生成（与 JSON / SSE 接口共用）。仅标准测试（及 OpenSSL 对照若执行）全部通过才算 success；generation_ok 表示是否已落盘生成（无异常）。

    各 provider 的 ``max_retries`` 与首页 ``/api/generate`` 一致（现为 3），本地与云端批量矩阵均执行最多 3 轮测试驱动改进闭环。

    若 ``local_batch_skip_enabled``（见 config.yaml）为真：先从 ``code_history.db`` 取该槽位成功记录并对历史源码做标准向量复测——
    **本地类 provider** 匹配任一本线路径上「本地成功」；**云端等非本地 provider** 仅匹配 **同名 provider** 的成功记录。
    标准向量复测通过则刷新该条时间戳并 **跳过 LLM 再生成**（``scripts/run_full_llm_matrix.py`` 等同 Web 批量）。
    传 ``generate_kwargs={"_skip_history_retest": True}`` 时跳过上述历史复测（用于蒸馏消融「无蒸馏」基线强制重跑）。
    传 ``generate_kwargs={"_history_only": True}`` 时：仅尝试历史复测，**无历史或复测失败则直接返回失败，不调用 LLM**（用于蒸馏消融增量更新「有蒸馏」列）。
    OpenSSL 对照未通过不阻止跳过（与本地批量一致）。

    Args:
        agent: 若传入（如蒸馏脚本已覆写 ``distillation.enabled``），则使用该实例并**不再**新建 ``CryptoAgent``；
        须与 ``provider`` 为同一线路；``generate_and_save`` 仍以 ``validate=False`` 调用，与 Web 批量一致。
    """
    t0 = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        if agent is None:
            agent = CryptoAgent(provider=provider, enable_validation=False)
        else:
            pv = (provider or "").strip().lower()
            av = (getattr(agent, "provider", None) or "").strip().lower()
            if pv and av and pv != av:
                logger.warning(
                    "批量：传入 agent.provider=%s 与参数 provider=%s 不一致，仍以传入 agent 为准",
                    getattr(agent, "provider", None),
                    provider,
                )

        _gen_kw = dict(generate_kwargs or {})
        _skip_history_retest = bool(_gen_kw.pop("_skip_history_retest", False))
        _history_only = bool(_gen_kw.pop("_history_only", False))

        _try_history = (_local_batch_skip_enabled() or _history_only) and not _skip_history_retest
        if _try_history:
            since_skip = _local_batch_skip_if_success_since()
            hm = HistoryManager()
            if _provider_is_local_batch(provider):
                rec = hm.get_latest_local_success_record_since(
                    config["algorithm"],
                    config.get("mode"),
                    config["language"],
                    since_skip,
                )
            else:
                rec = hm.get_latest_success_record_since_for_provider(
                    config["algorithm"],
                    config.get("mode"),
                    config["language"],
                    since_skip,
                    provider,
                )
            hist_code = rec.get("code") if rec else None
            hist_op = (rec.get("operation") or "加密解密") if rec else "加密解密"
            if isinstance(hist_code, str) and hist_code.strip():
                if _provider_is_local_batch(provider):
                    async with _LOCAL_BATCH_SEM:
                        tr, otr = await rerun_vector_tests_on_code(
                            agent,
                            hist_code,
                            config["algorithm"],
                            config.get("mode"),
                            config["language"],
                            operation=str(hist_op),
                        )
                else:
                    tr, otr = await rerun_vector_tests_on_code(
                        agent,
                        hist_code,
                        config["algorithm"],
                        config.get("mode"),
                        config["language"],
                        operation=str(hist_op),
                    )
                # 复用历史：仅以标准向量测试为准；OpenSSL 对照在 OpenSSL 3 无 legacy 等环境下可能失败，不阻止跳过生成
                ok_retest = tr is not None and tr[0]
                if ok_retest and otr is not None and not otr[0]:
                    logger.info(
                        "批量：历史代码标准向量已通过，OpenSSL 对照未通过（仍跳过 LLM）: %s",
                        (otr[1] if len(otr) > 1 else "")[:500],
                    )
                if ok_retest:
                    hm.refresh_history_timestamp(rec["id"])
                    logger.info(
                        "批量：历史代码复测通过，跳过生成 provider=%s (%s %s %s)",
                        provider,
                        config["algorithm"],
                        config.get("mode"),
                        config["language"],
                    )
                    fn = rec.get("filename")
                    fname = fn if isinstance(fn, str) and fn.strip() else None
                    return _batch_result_pack(
                        config,
                        success=True,
                        generation_ok=True,
                        vector_status="通过",
                        vector_detail="历史代码复测通过，未调用 LLM",
                        error=None,
                        code=hist_code,
                        filename=fname,
                        total_ms=elapsed_ms(),
                    )
                logger.info(
                    "批量：历史记录存在但复测未通过或缺少测试数据，将重新生成 provider=%s (%s %s %s)",
                    provider,
                    config["algorithm"],
                    config.get("mode"),
                    config["language"],
                )
                if _history_only:
                    fail_msg = (
                        tr[1] if (tr is not None and len(tr) > 1 and tr[1]) else "历史复测未通过"
                    )
                    return _batch_result_pack(
                        config,
                        success=False,
                        generation_ok=False,
                        vector_status="未通过",
                        vector_detail=str(fail_msg)[:500],
                        error=str(fail_msg),
                        code=hist_code if isinstance(hist_code, str) else None,
                        filename=None,
                        total_ms=elapsed_ms(),
                    )
            elif _history_only:
                return _batch_result_pack(
                    config,
                    success=False,
                    generation_ok=False,
                    vector_status="跳过",
                    vector_detail="无可用历史成功记录（history_only，未调用 LLM）",
                    error="无可用历史成功记录（history_only，未调用 LLM）",
                    code=None,
                    filename=None,
                    total_ms=elapsed_ms(),
                )

        # 与首页 /api/generate 对齐：默认最多 3 轮整段 generate-测试闭环；generate_kwargs.max_retries 可覆盖（如蒸馏消融无蒸馏基线=1）。
        _batch_retries = int(_gen_kw.pop("max_retries", 3))

        if _skip_history_retest:
            logger.info(
                "批量：跳过历史复测，强制重新生成 provider=%s (%s %s %s)",
                provider,
                config["algorithm"],
                config.get("mode"),
                config["language"],
            )

        if _batch_retries != 3:
            logger.info(
                "批量：max_retries=%s provider=%s (%s %s %s)",
                _batch_retries,
                provider,
                config["algorithm"],
                config.get("mode"),
                config["language"],
            )

        if _provider_is_local_batch(provider):
            async with _LOCAL_BATCH_SEM:
                result_tuple = await agent.generate_and_save(
                    algorithm=config["algorithm"],
                    mode=config.get("mode"),
                    language=config["language"],
                    validate=False,
                    max_retries=_batch_retries,
                    **_gen_kw,
                )
        else:
            result_tuple = await agent.generate_and_save(
                algorithm=config["algorithm"],
                mode=config.get("mode"),
                language=config["language"],
                validate=False,
                max_retries=_batch_retries,
                **_gen_kw,
            )
        filepath, _validation_result, test_result, openssl_test_result = result_tuple
        code: Optional[str] = None
        filename: Optional[str] = None
        try:
            with file_lock:
                with open(filepath, "r", encoding="utf-8") as f:
                    code = f.read()
            filename = filepath.name
        except Exception:
            pass

        if test_result is None:
            err = "未执行标准测试或缺少测试数据，批量任务不记为成功"
            return _batch_result_pack(
                config,
                success=False,
                generation_ok=True,
                vector_status="跳过",
                vector_detail="no_standard_test_data",
                error=err,
                code=None,
                filename=None,
                total_ms=elapsed_ms(),
                generated_code=code,
                generated_filename=filename,
            )
        if not test_result[0]:
            msg = test_result[1] if len(test_result) > 1 else "标准测试未通过"
            msg_s = str(msg)
            return _batch_result_pack(
                config,
                success=False,
                generation_ok=True,
                vector_status="未通过",
                vector_detail=msg_s[:500],
                error=msg_s,
                code=None,
                filename=None,
                total_ms=elapsed_ms(),
                generated_code=code,
                generated_filename=filename,
            )
        if openssl_test_result is not None and not openssl_test_result[0]:
            msg = openssl_test_result[1] if len(openssl_test_result) > 1 else "OpenSSL对照测试未通过"
            msg_s = str(msg)
            return _batch_result_pack(
                config,
                success=False,
                generation_ok=True,
                vector_status="未通过",
                vector_detail=msg_s[:500],
                error=msg_s,
                code=None,
                filename=None,
                total_ms=elapsed_ms(),
                generated_code=code,
                generated_filename=filename,
            )
        return _batch_result_pack(
            config,
            success=True,
            generation_ok=True,
            vector_status="通过",
            vector_detail="",
            error=None,
            code=code,
            filename=filename,
            total_ms=elapsed_ms(),
        )
    except Exception as e:
        logger.error(f"配置 {config} 生成失败: {e}")
        return _batch_result_pack(
            config,
            success=False,
            generation_ok=False,
            vector_status="—",
            vector_detail="",
            error=str(e),
            code=None,
            filename=None,
            total_ms=elapsed_ms(),
        )

# 任务管理：存储正在运行的任务和取消标志
# 注意：每个请求都会创建独立的Agent实例，支持真正的并发执行
running_tasks: Dict[str, Dict[str, Any]] = {}  # task_id -> {cancelled: bool, provider: str}
task_lock = threading.Lock()  # 用于保护 running_tasks 的并发访问


class GenerateRequest(BaseModel):
    """代码生成请求模型"""
    provider: str
    algorithm: str
    mode: Optional[str] = None
    language: str = "python"
    operation: str = "加密解密"
    enable_validation: bool = True
    requirements: Optional[str] = None
    task_id: Optional[str] = None  # 任务ID，用于取消


class CancelTaskRequest(BaseModel):
    """取消任务请求模型"""
    task_id: str


class TestConnectionRequest(BaseModel):
    """API连接测试请求模型"""
    provider: str


class APIKeysRequest(BaseModel):
    """API密钥保存请求模型"""
    keys: Dict[str, str]


class SaveCodeRequest(BaseModel):
    """保存代码请求模型"""
    filename: str
    code: str


class TestCodeRequest(BaseModel):
    """代码测试请求模型"""
    code: str
    language: str
    # 可选：用于 Python 等场景的评测前清洗（如 AES+OFB）
    algorithm: Optional[str] = None
    mode: Optional[str] = None
    plaintext: Optional[str] = None
    expected_ciphertext: Optional[str] = None
    ciphertext: Optional[str] = None
    expected_plaintext: Optional[str] = None
    # 密钥相关（可选）
    key: Optional[str] = None
    # IV相关（可选，仅某些模式需要）
    iv: Optional[str] = None
    # RSA相关（可选）
    public_key_n: Optional[str] = None  # 公钥模数n
    public_key_e: Optional[str] = None   # 公钥指数e
    private_key_n: Optional[str] = None  # 私钥模数n
    private_key_d: Optional[str] = None  # 私钥指数d
    signature: Optional[str] = None  # RSA签名
    # SM4相关（可选）
    data_ciphertext: Optional[str] = None  # 数据密文
    key_ciphertext: Optional[str] = None  # 密钥密文
    digital_certificate: Optional[str] = None  # 数字证书
    sm4_signature: Optional[str] = None  # SM4数字签名


class ImproveCodeRequest(BaseModel):
    """代码改进请求模型"""
    code: str
    language: str
    algorithm: str
    mode: Optional[str] = None
    operation: str = "加密解密"
    provider: str
    # 测试反馈信息
    test_feedback: Dict[str, Any]


class CompareCodeRequest(BaseModel):
    """代码对比请求模型"""
    code1_id: str
    code2_id: str


class AnalyzePerformanceRequest(BaseModel):
    """性能分析请求模型"""
    code: str
    language: str
    data_size: int = 1024
    iterations: int = 10


class ExplainCodeRequest(BaseModel):
    """代码解释请求模型"""
    code: str
    language: str
    depth: str = "detailed"
    algorithm: Optional[str] = None
    mode: Optional[str] = None
    provider: Optional[str] = None  # LLM提供商，可选，如果不提供则使用默认值


class BatchGenerateRequest(BaseModel):
    """批量生成请求模型"""
    configs: list
    provider: str


class SecurityScanRequest(BaseModel):
    """安全扫描请求模型"""
    code: str
    language: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """API密钥配置页面"""
    return templates.TemplateResponse("config.html", {"request": request})


@app.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    """代码对比页面"""
    return templates.TemplateResponse("compare.html", {"request": request})


@app.get("/analyze", response_class=HTMLResponse)
async def analyze_page(request: Request):
    """性能分析页面"""
    return templates.TemplateResponse("analyze.html", {"request": request})


@app.get("/explain", response_class=HTMLResponse)
async def explain_page(request: Request):
    """AI代码解释页面"""
    return templates.TemplateResponse("explain.html", {"request": request})


@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request):
    """代码模板库页面"""
    return templates.TemplateResponse("templates.html", {"request": request})


@app.get("/batch", response_class=HTMLResponse)
async def batch_page(request: Request):
    """批量生成页面"""
    return templates.TemplateResponse("batch.html", {"request": request})


@app.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    """安全扫描页面"""
    return templates.TemplateResponse("security.html", {"request": request})


@app.get("/openssl-test", response_class=HTMLResponse)
async def openssl_test_page(request: Request):
    """OpenSSL标准测试页面"""
    return templates.TemplateResponse("openssl_test.html", {"request": request})


@app.get("/openssl-code", response_class=HTMLResponse)
async def openssl_code_page(request: Request):
    """OpenSSL标准代码库页面"""
    return templates.TemplateResponse("openssl_code.html", {"request": request})


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """历史记录页面"""
    return templates.TemplateResponse("history.html", {"request": request})


@app.get("/api/history")
async def get_history(
    limit: Optional[int] = Query(None, description="返回的最大记录数"),
    algorithm: Optional[str] = Query(None, description="按算法筛选"),
    language: Optional[str] = Query(None, description="按语言筛选"),
    provider: Optional[str] = Query(None, description="按提供商筛选"),
    mode: Optional[str] = Query(None, description="按模式筛选；__none__ 表示仅无模式"),
):
    """获取历史记录列表"""
    try:
        from utils.history_manager import HistoryManager
        history_manager = HistoryManager()
        history = history_manager.get_history(
            limit=limit, 
            reverse=True,
            algorithm=algorithm,
            language=language,
            provider=provider,
            mode=mode,
        )
        return JSONResponse({
            'success': True,
            'history': history,
            'count': len(history)
        })
    except Exception as e:
        logger.error(f"获取历史记录失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/history/statistics")
async def get_history_statistics():
    """获取历史记录统计信息"""
    try:
        from utils.history_manager import HistoryManager
        history_manager = HistoryManager()
        stats = history_manager.get_statistics()
        return JSONResponse({
            'success': True,
            'statistics': stats
        })
    except Exception as e:
        logger.error(f"获取历史记录统计失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/history/{history_id}")
async def get_history_item(history_id: str):
    """获取单个历史记录项"""
    try:
        from utils.history_manager import HistoryManager
        history_manager = HistoryManager()
        item = history_manager.get_history_by_id(history_id)
        if item:
            return JSONResponse({
                'success': True,
                'item': item
            })
        else:
            return JSONResponse({
                'success': False,
                'error': '历史记录不存在'
            }, status_code=404)
    except Exception as e:
        logger.error(f"获取历史记录项失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.delete("/api/history/{history_id}")
async def delete_history_item(history_id: str):
    """删除历史记录项"""
    try:
        from utils.history_manager import HistoryManager
        history_manager = HistoryManager()
        success = history_manager.delete_history(history_id)
        if success:
            return JSONResponse({
                'success': True,
                'message': '已删除'
            })
        else:
            return JSONResponse({
                'success': False,
                'error': '历史记录不存在'
            }, status_code=404)
    except Exception as e:
        logger.error(f"删除历史记录失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.post("/api/validate-history/{history_id}")
async def validate_history(history_id: str):
    """验证历史记录中的代码"""
    try:
        from utils.history_manager import HistoryManager
        history_manager = HistoryManager()
        
        # 获取历史记录
        history_item = history_manager.get_history_by_id(history_id)
        if not history_item:
            return JSONResponse(
                {'success': False, 'error': '历史记录不存在'},
                status_code=404
            )
        
        # 验证代码
        validator = CodeValidator()
        success, message = validator.validate(
            code=history_item['code'],
            language=history_item['language']
        )
        
        # 更新历史记录的验证状态
        history_manager.update_history(history_id, validation_success=success)
        
        return JSONResponse({
            'success': True,
            'validation_success': success,
            'message': message,
            'validation_result': {
                'success': success,
                'message': message
            }
        })
    except Exception as e:
        logger.error(f"验证历史记录失败: {e}")
        return JSONResponse(
            {'success': False, 'error': str(e)},
            status_code=500
        )


@app.delete("/api/history")
async def clear_history():
    """清空所有历史记录"""
    try:
        from utils.history_manager import HistoryManager
        history_manager = HistoryManager()
        count = history_manager.clear_history()
        return JSONResponse({
            'success': True,
            'message': f'已清空 {count} 条历史记录'
        })
    except Exception as e:
        logger.error(f"清空历史记录失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/test-data")
async def get_test_data(algorithm: str = Query(..., description="算法名称，如 DES, AES, RSA, SM4"), 
                        mode: Optional[str] = Query(None, description="模式，如 ECB, CBC, CFB, OFB, GCM, CTR")):
    """获取指定算法和模式的测试数据"""
    try:
        project_root = Path(__file__).parent.parent
        test_data_file = project_root / "test_data.yaml"
        if not test_data_file.exists():
            return JSONResponse({
                'success': False,
                'error': '测试数据文件不存在'
            }, status_code=404)
        
        with open(test_data_file, 'r', encoding='utf-8') as f:
            test_data = yaml.safe_load(f) or {}
        
        algorithm_upper = algorithm.upper()
        if algorithm_upper not in test_data:
            return JSONResponse({
                'success': False,
                'error': f'算法 {algorithm} 的测试数据不存在'
            }, status_code=404)
        
        algo_data = test_data[algorithm_upper]
        result = {}
        
        # 对于RSA，返回RSA特定的数据
        if algorithm_upper == 'RSA':
            result = {
                'plaintext': algo_data.get('plaintext'),
                'public_key': algo_data.get('public_key', {}),
                'private_key': algo_data.get('private_key', {}),
                'ciphertexts': algo_data.get('ciphertexts', {})
            }
        # 对于SM4，返回SM4特定的数据
        elif algorithm_upper == 'SM4':
            mode_upper = (mode or 'ECB').upper()
            expected_ciphertext = None
            if 'ciphertexts' in algo_data and mode_upper in algo_data['ciphertexts']:
                expected_ciphertext = algo_data['ciphertexts'][mode_upper]
            elif 'data_ciphertext' in algo_data:
                expected_ciphertext = algo_data['data_ciphertext']
            
            result = {
                'plaintext': algo_data.get('plaintext'),
                'key': algo_data.get('key'),
                'iv': algo_data.get('iv'),
                'data_ciphertext': algo_data.get('data_ciphertext'),
                'key_ciphertext': algo_data.get('key_ciphertext'),
                'digital_certificate': algo_data.get('digital_certificate'),
                'signature': algo_data.get('signature'),
                'ciphertexts': algo_data.get('ciphertexts', {}),
                'expected_ciphertext': expected_ciphertext
                }
        # 对于DES和AES，返回通用数据
        else:
            mode_upper = (mode or 'ECB').upper()
            expected_ciphertext = None
            if 'ciphertexts' in algo_data and mode_upper in algo_data['ciphertexts']:
                expected_ciphertext = algo_data['ciphertexts'][mode_upper]
            
            result = {
                'plaintext': algo_data.get('plaintext'),
                'key': algo_data.get('key'),
                'iv': algo_data.get('iv'),
                'expected_ciphertext': expected_ciphertext,
                'ciphertexts': algo_data.get('ciphertexts', {})
            }
            
            # 对于GCM模式，添加iv_gcm字段（如果存在）
            if mode_upper == 'GCM' and 'iv_gcm' in algo_data:
                result['iv_gcm'] = algo_data['iv_gcm']
            
            # 添加modes字段（如果存在）- 包含每个模式的独立测试数据
            if 'modes' in algo_data:
                result['modes'] = algo_data['modes']
        
        return JSONResponse({
            'success': True,
            'test_data': result
        })
    except Exception as e:
        logger.error(f"获取测试数据失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/test-data/sm4")
async def get_sm4_test_data():
    """获取SM4测试数据的预设值（兼容旧接口）"""
    result = await get_test_data(algorithm='SM4', mode='ECB')
    return result


@app.get("/test-data", response_class=HTMLResponse)
async def test_data_page(request: Request):
    """标准测试库页面"""
    try:
        # 加载测试数据
        project_root = Path(__file__).parent.parent
        test_data_file = project_root / "test_data.yaml"
        openssl_test_data_file = project_root / "openssl_test_data.yaml"
        
        test_data = {}
        if test_data_file.exists():
            with open(test_data_file, 'r', encoding='utf-8') as f:
                test_data = yaml.safe_load(f) or {}
        
        # 加载OpenSSL官方测试数据
        openssl_test_data = {}
        if openssl_test_data_file.exists():
            with open(openssl_test_data_file, 'r', encoding='utf-8') as f:
                openssl_test_data = yaml.safe_load(f) or {}
        
        # 将OpenSSL官方测试数据合并到标准测试数据中
        # 对于每个算法，如果OpenSSL数据中有对应的模式，添加到openssl_modes字段
        for algo_name, algo_data in test_data.items():
            if algo_name in openssl_test_data:
                openssl_algo_data = openssl_test_data[algo_name]
                # 将OpenSSL官方测试数据添加到openssl_modes字段
                if 'openssl_modes' not in algo_data:
                    algo_data['openssl_modes'] = {}
                
                # 遍历OpenSSL数据中的所有模式
                for mode_name, mode_data in openssl_algo_data.items():
                    # 跳过顶层字段，只处理模式数据
                    if mode_name not in ['plaintext', 'key', 'iv', 'ciphertexts']:
                        mode_upper = mode_name.upper()
                        # 如果mode_data是字典（包含plaintext、key等），直接使用
                        if isinstance(mode_data, dict):
                            algo_data['openssl_modes'][mode_upper] = mode_data
                        # 如果mode_data是字符串（可能是旧的格式），跳过
        
        return templates.TemplateResponse("test_data.html", {
            "request": request,
            "test_data": test_data
        })
    except Exception as e:
        logger.error(f"加载测试数据失败: {e}")
        return templates.TemplateResponse("test_data.html", {
            "request": request,
            "test_data": {}
        })


@app.get("/openssl-test-data", response_class=HTMLResponse)
async def openssl_test_data_page(request: Request):
    """OpenSSL官方测试数据页面"""
    try:
        # 加载OpenSSL官方测试数据
        project_root = Path(__file__).parent.parent
        openssl_test_data_file = project_root / "openssl_test_data.yaml"
        openssl_test_data = {}
        if openssl_test_data_file.exists():
            with open(openssl_test_data_file, 'r', encoding='utf-8') as f:
                openssl_test_data = yaml.safe_load(f) or {}
        return templates.TemplateResponse("openssl_test_data.html", {
            "request": request,
            "openssl_test_data": openssl_test_data
        })
    except Exception as e:
        logger.error(f"加载OpenSSL测试数据失败: {e}")
        return templates.TemplateResponse("openssl_test_data.html", {
            "request": request,
            "openssl_test_data": {}
        })


@app.get("/api/providers")
async def get_providers():
    """获取可用的LLM提供商列表"""
    try:
        config = ConfigLoader()
        llm_providers = config._config.get('llm_providers', {})
        default_provider = config.get('default_provider', 'deepseek')
        
        providers = []
        for provider, config_data in llm_providers.items():
            enabled = bool(config_data.get("enabled", False))
            has_key = llm_provider_key_ready(
                config_data,
                api_key_manager.get_key,
            )

            providers.append(
                {
                    "name": provider,
                    "display_name": llm_provider_display_name(provider),
                    "has_api_key": has_key,
                    "is_default": provider == default_provider,
                    "enabled": enabled,
                }
            )
        
        return JSONResponse({
            'success': True,
            'providers': providers,
            'default_provider': default_provider
        })
    except Exception as e:
        logger.error(f"获取提供商列表失败: {e}")
        return JSONResponse(
            {'success': False, 'error': str(e)},
            status_code=500
        )


@app.get("/api/algorithms")
async def get_algorithms():
    """获取支持的算法列表"""
    try:
        config = ConfigLoader()
        return JSONResponse({
            'success': True,
            'algorithms': {
                'DES': config.get('des_modes', []),
                'AES': config.get('aes_modes', []),
                'RSA': [],  # RSA没有模式，只有操作类型（通过operation字段指定）
                'SM4': ['ECB', 'CBC', 'CFB', 'OFB']
            }
        })
    except Exception as e:
        logger.error(f"获取算法列表失败: {e}")
        return JSONResponse(
            {'success': False, 'error': str(e)},
            status_code=500
        )


@app.get("/api/languages")
async def get_languages():
    """获取支持的编程语言列表"""
    return JSONResponse({
        'success': True,
        'languages': ['python', 'c', 'cpp']
    })


@app.post("/api/test-connection")
async def test_connection(request: TestConnectionRequest):
    """测试API连接（轻量探活：不初始化 CryptoAgent，不加载 prompt 模板）"""
    try:
        cfg = ConfigLoader("config.yaml")
        llm_config = cfg.get_llm_config(request.provider)
        if not llm_config.get('enabled', False):
            return JSONResponse({
                'success': False,
                'message': f'LLM提供商 {request.provider} 未启用'
            })

        llm = LLMAdapter(request.provider, llm_config)
        # 保持超短探活请求，避免触发任何大上下文逻辑
        response = await llm.generate("OK", "reply OK only")
        success = bool(response)
        message = "API连接测试成功" if success else "API返回空响应"
        
        return JSONResponse({
            'success': success,
            'message': message
        })
    except ImportError as e:
        error_msg = str(e)
        missing_module = None
        
        # 检测缺失的模块
        if 'openai' in error_msg:
            missing_module = 'openai'
        elif 'anthropic' in error_msg:
            missing_module = 'anthropic'
        elif 'deepseek' in error_msg:
            missing_module = 'deepseek'
        
        if missing_module:
            friendly_error = f"缺少依赖模块: {missing_module}。请运行：pip install {missing_module}"
        else:
            friendly_error = f"缺少依赖模块。请运行：pip install -r requirements.txt"
        
        logger.error(f"API连接测试失败: {error_msg}")
        return JSONResponse({
            'success': False,
            'message': friendly_error
        })
    except Exception as e:
        error_msg = str(e)
        logger.error(f"API连接测试失败: {error_msg}")
        
        # 检查是否是模块缺失错误
        if 'No module named' in error_msg:
            module_name = error_msg.split("'")[1] if "'" in error_msg else "未知模块"
            friendly_error = f"缺少依赖模块: {module_name}。请运行：pip install {module_name}"
        else:
            friendly_error = f'连接测试失败: {error_msg}'
        
        return JSONResponse({
            'success': False,
            'message': friendly_error
        })


@app.post("/api/generate")
async def generate_code(request: GenerateRequest):
    """
    生成代码 - 支持多页面并发执行
    
    每个请求都会创建独立的Agent实例，支持：
    - 多个页面同时使用相同的LLM进行代码生成
    - 多个页面同时使用不同的LLM进行代码生成
    - 真正的异步并发执行，不会相互阻塞
    """
    # 生成任务ID
    task_id = request.task_id or str(uuid.uuid4())
    
    # 为每个请求创建独立的Agent实例，支持真正的并发
    # 这样多个页面可以同时使用相同或不同的LLM，互不干扰
    agent = CryptoAgent(
        provider=request.provider,
        enable_validation=request.enable_validation,
        enable_testing=True  # 启用自动测试
    )
    
    # 注册任务
    with task_lock:
        running_tasks[task_id] = {
            'cancelled': False,
            'provider': request.provider  # 仅存储provider用于日志
        }
    
    try:
        # 准备参数
        kwargs = {}
        if request.requirements:
            kwargs['额外要求'] = request.requirements
        
        # 添加任务ID到kwargs，以便在生成过程中检查取消标志
        kwargs['_task_id'] = task_id
        
        logger.info(f"任务 {task_id} 开始生成代码 - Provider: {request.provider}, Algorithm: {request.algorithm}, Language: {request.language}")
        
        # 生成代码（会自动测试并重试直到通过）；本地线路串行化，避免 Ollama 多请求堆积超时
        if _provider_is_local_batch(request.provider):
            async with _LOCAL_BATCH_SEM:
                result_tuple = await agent.generate_and_save(
                    algorithm=request.algorithm,
                    mode=request.mode,
                    operation=request.operation,
                    language=request.language,
                    validate=request.enable_validation,
                    max_retries=3,
                    **kwargs
                )
        else:
            result_tuple = await agent.generate_and_save(
                algorithm=request.algorithm,
                mode=request.mode,
                operation=request.operation,
                language=request.language,
                validate=request.enable_validation,
                max_retries=3,
                **kwargs
            )
        
        # 检查是否被取消
        with task_lock:
            if task_id in running_tasks and running_tasks[task_id]['cancelled']:
                logger.info(f"任务 {task_id} 已被用户取消")
                return JSONResponse({
                    'success': False,
                    'error': '任务已被用户取消',
                    'cancelled': True
                })
        
        # 解包结果（可能是2个、3个或4个元素）
        if len(result_tuple) == 2:
            filepath, validation_result = result_tuple
            test_result = None
            openssl_test_result = None
        elif len(result_tuple) == 3:
            filepath, validation_result, test_result = result_tuple
            openssl_test_result = None
        else:
            filepath, validation_result, test_result, openssl_test_result = result_tuple
        
        # 读取生成的代码（线程安全）；仅当验证/测试/OpenSSL 对照（若有）均通过时才下发给前端
        with file_lock:
            with open(filepath, 'r', encoding='utf-8') as f:
                code = f.read()

        err_lines: List[str] = []
        pipeline_ok = True
        if request.enable_validation and validation_result is not None and not validation_result[0]:
            pipeline_ok = False
            vo = validation_result[1] if len(validation_result) > 1 else ""
            err_lines.append(f"代码验证未通过: {str(vo)[:1200]}")
        if test_result is not None and not test_result[0]:
            pipeline_ok = False
            tm = test_result[1] if len(test_result) > 1 else ""
            err_lines.append(f"标准测试未通过: {str(tm)[:1200]}")
        if openssl_test_result is not None and not openssl_test_result[0]:
            pipeline_ok = False
            om = openssl_test_result[1] if len(openssl_test_result) > 1 else ""
            err_lines.append(f"OpenSSL 对照测试未通过: {str(om)[:1200]}")

        result: Dict[str, Any] = {
            'success': pipeline_ok,
            'filepath': str(filepath) if pipeline_ok else None,
            'filename': filepath.name if pipeline_ok else None,
            'code': code if pipeline_ok else '',
            'validation': None,
            'test_result': None,
            'openssl_test_result': None,
            'error': None if pipeline_ok else ("\n\n".join(err_lines) if err_lines else "生成未通过验证或测试"),
        }

        if validation_result:
            success, output = validation_result
            result['validation'] = {
                'success': success,
                'output': output
            }

        if test_result:
            test_success, test_message, test_details = test_result
            result['test_result'] = {
                'success': test_success,
                'message': test_message,
                'details': test_details
            }

        if openssl_test_result:
            openssl_success, openssl_message, openssl_details = openssl_test_result
            result['openssl_test_result'] = {
                'success': openssl_success,
                'message': openssl_message,
                'details': openssl_details
            }

        result['task_id'] = task_id
        return JSONResponse(result)
        
    except asyncio.CancelledError:
        logger.info(f"任务 {task_id} 被取消")
        return JSONResponse({
            'success': False,
            'error': '任务已被用户取消',
            'cancelled': True
        })
    except ImportError as e:
        error_msg = str(e)
        missing_module = None
        
        # 检测缺失的模块
        if 'openai' in error_msg:
            missing_module = 'openai'
        elif 'anthropic' in error_msg:
            missing_module = 'anthropic'
        elif 'deepseek' in error_msg:
            missing_module = 'deepseek'
        
        if missing_module:
            friendly_error = f"缺少依赖模块: {missing_module}。请运行以下命令安装：\npip install {missing_module}\n或者安装所有依赖：\npip install -r requirements.txt"
        else:
            friendly_error = f"缺少依赖模块。请运行：pip install -r requirements.txt\n错误详情: {error_msg}"
        
        logger.error(f"代码生成失败: {error_msg}")
        return JSONResponse(
            {
                'success': False,
                'error': friendly_error
            },
            status_code=500
        )
    except ValueError as e:
        # 处理输入tokens超过限制的错误
        error_msg = str(e)
        logger.error(f"任务 {task_id} 生成代码失败: {error_msg}")
        
        if '输入tokens' in error_msg or '上下文限制' in error_msg or '输入内容过长' in error_msg:
            friendly_error = (
                f"输入内容过长，超过了模型的上下文限制。\n\n"
                f"解决方案：\n"
                f"1. 使用支持更大上下文的模型（推荐）：\n"
                f"   - 在 config.yaml 中将 gpt-4 改为 gpt-4-turbo（支持128K上下文）\n"
                f"   - 或使用 Claude 3.5 Sonnet（支持200K上下文）\n"
                f"2. 减少额外要求：如果设置了额外要求，请简化或删除\n"
                f"3. 使用更简单的语言：C/C++ 的 prompt 通常比 Python 更长\n\n"
                f"错误详情：{error_msg}"
            )
        else:
            friendly_error = error_msg
        
        return JSONResponse(
            {
                'success': False,
                'error': friendly_error
            },
            status_code=400  # 使用400状态码表示客户端错误
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"任务 {task_id} 生成代码失败: {error_msg}", exc_info=True)
        
        # 检查是否是模块缺失错误
        if 'No module named' in error_msg:
            module_name = error_msg.split("'")[1] if "'" in error_msg else "未知模块"
            friendly_error = f"缺少依赖模块: {module_name}。请运行：pip install {module_name}\n或安装所有依赖：pip install -r requirements.txt"
        # 检查是否是模型不存在错误（豆包等）
        elif 'does not exist' in error_msg or 'NotFound' in error_msg or '模型' in error_msg or 'endpoint' in error_msg.lower():
            friendly_error = error_msg
        else:
            friendly_error = error_msg
        
        return JSONResponse(
            {
                'success': False,
                'error': friendly_error
            },
            status_code=500
        )
    finally:
        # 清理任务
        with task_lock:
            if task_id in running_tasks:
                del running_tasks[task_id]
        logger.info(f"任务 {task_id} 完成")


@app.post("/api/cancel-task")
async def cancel_task(request: CancelTaskRequest):
    """取消正在运行的代码生成任务"""
    try:
        with task_lock:
            if request.task_id in running_tasks:
                running_tasks[request.task_id]['cancelled'] = True
                return JSONResponse({
                    'success': True,
                    'message': '任务取消请求已发送'
                })
            else:
                return JSONResponse({
                    'success': False,
                    'error': '任务不存在或已完成'
                })
    except Exception as e:
        logger.error(f"取消任务失败: {e}")
        return JSONResponse({
            'success': False,
            'error': f'取消任务失败: {str(e)}'
        })


@app.get("/api/download/{filename:path}")
async def download_file(filename: str):
    """下载生成的代码文件"""
    try:
        config = ConfigLoader()
        output_dir = Path(config.get('output_dir', './generated_code'))
        filepath = output_dir / filename
        
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
        
        return FileResponse(
            path=str(filepath),
            filename=filename,
            media_type='text/plain'
        )
    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/save-code")
async def save_code(request: SaveCodeRequest):
    """保存编辑后的代码到文件"""
    try:
        config = ConfigLoader()
        output_dir = Path(config.get('output_dir', './generated_code'))
        filepath = output_dir / request.filename
        
        # 安全检查：确保文件在output_dir内
        try:
            filepath.resolve().relative_to(output_dir.resolve())
        except ValueError:
            return JSONResponse({
                'success': False,
                'error': '无效的文件路径'
            }, status_code=400)
        
        # 确保输出目录存在
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 线程安全的文件写入
        with file_lock:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(request.code)
        
        logger.info(f"代码已保存到: {filepath}")
        
        return JSONResponse({
            'success': True,
            'message': '代码已保存',
            'filename': request.filename
        })
    except Exception as e:
        logger.error(f"保存代码失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/api-keys")
async def get_api_keys():
    """获取所有API密钥配置（不返回实际密钥值）"""
    try:
        config = ConfigLoader()
        llm_providers = config._config.get('llm_providers', {})
        
        keys_info = []
        for provider, config_data in llm_providers.items():
            api_key_env = config_data.get("api_key_env", "") or ""
            if not api_key_env and not config_data.get("api_key_optional"):
                continue
            enabled = bool(config_data.get("enabled", False))
            has_key = llm_provider_key_ready(
                config_data,
                api_key_manager.get_key,
            )

            keys_info.append(
                {
                    "provider": provider,
                    "display_name": llm_provider_display_name(provider),
                    "env_name": api_key_env,
                    "has_key": has_key,
                    "enabled": enabled,
                }
            )
        
        return JSONResponse({
            'success': True,
            'keys': keys_info
        })
    except Exception as e:
        logger.error(f"获取API密钥信息失败: {e}")
        return JSONResponse(
            {'success': False, 'error': str(e)},
            status_code=500
        )


@app.post("/api/api-keys")
async def save_api_keys(request: APIKeysRequest):
    """保存API密钥"""
    try:
        success = api_key_manager.set_keys(request.keys)
        
        if success:
            # 注意：由于每个请求都创建独立的Agent实例，不需要清除缓存
            # 新的API密钥会在下次创建Agent时自动使用
            
            return JSONResponse({
                'success': True,
                'message': 'API密钥保存成功'
            })
        else:
            return JSONResponse(
                {'success': False, 'error': '保存API密钥失败'},
                status_code=500
            )
    except Exception as e:
        logger.error(f"保存API密钥失败: {e}")
        return JSONResponse(
            {'success': False, 'error': str(e)},
            status_code=500
        )


@app.post("/api/test-code")
async def test_code(request: TestCodeRequest):
    """测试代码"""
    try:
        tester = CodeTester()
        
        # 验证输入
        if not request.code:
            return JSONResponse(
                {'success': False, 'error': '请提供代码'},
                status_code=400
            )
        
        if not ((request.plaintext and request.expected_ciphertext) or 
                (request.ciphertext and request.expected_plaintext)):
            return JSONResponse(
                {'success': False, 'error': '请提供测试数据（明文+预期密文 或 密文+预期明文）'},
                status_code=400
            )
        
        # 不再自动纠正预期密文，使用用户提供的预期密文
        # （已移除自动检测和纠正逻辑，避免误判）
        
        # 如果是SM4算法且用户未提供某些字段，使用预设值
        data_ciphertext = request.data_ciphertext
        key_ciphertext = request.key_ciphertext
        digital_certificate = request.digital_certificate
        sm4_signature = request.sm4_signature
        
        # 检查代码中是否包含SM4相关关键词
        code_lower = request.code.lower()
        is_sm4 = 'sm4' in code_lower or 'SM4' in request.code
        
        if is_sm4:
            # 加载SM4预设值
            project_root = Path(__file__).parent.parent
            test_data_file = project_root / "test_data.yaml"
            if test_data_file.exists():
                with open(test_data_file, 'r', encoding='utf-8') as f:
                    test_data = yaml.safe_load(f) or {}
                sm4_data = test_data.get('SM4', {})
                
                # 如果用户未提供，使用预设值
                if not data_ciphertext:
                    data_ciphertext = sm4_data.get('data_ciphertext')
                if not key_ciphertext:
                    key_ciphertext = sm4_data.get('key_ciphertext')
                if not digital_certificate:
                    digital_certificate = sm4_data.get('digital_certificate')
                if not sm4_signature:
                    sm4_signature = sm4_data.get('signature')
        
        # 执行测试
        success, message, details = tester.test(
            code=request.code,
            language=request.language,
            plaintext=request.plaintext,
            expected_ciphertext=request.expected_ciphertext,
            ciphertext=request.ciphertext,
            expected_plaintext=request.expected_plaintext,
            key=request.key,
            iv=request.iv,
            public_key_n=request.public_key_n,
            public_key_e=request.public_key_e,
            private_key_n=request.private_key_n,
            private_key_d=request.private_key_d,
            signature=request.signature,
            algorithm=request.algorithm,
            mode=request.mode,
        )
        
        # 确保details是字典类型
        if not isinstance(details, dict):
            details = {}
        
        logger.info(f"测试结果 - success: {success}, details: {details}")
        
        return JSONResponse({
            'success': success,
            'message': message,
            'details': details  # 包含实际结果、预期结果等详细信息
        })
        
    except Exception as e:
        logger.error(f"代码测试失败: {e}")
        import traceback
        logger.error(f"错误详情: {traceback.format_exc()}")
        return JSONResponse(
            {'success': False, 'error': str(e), 'details': {}},
            status_code=500
        )


@app.post("/api/improve-code")
async def improve_code(request: ImproveCodeRequest):
    """
    基于测试反馈改进代码 - 支持多页面并发执行
    
    每个请求都会创建独立的Agent实例，支持真正的并发
    """
    try:
        # 为每个请求创建独立的Agent实例，支持真正的并发
        agent = CryptoAgent(
                    provider=request.provider,
                    enable_validation=False  # 改进时暂时不验证，让用户测试后再决定
                )
        
        # 调用改进方法
        improved_code = await agent.improve_code(
            original_code=request.code,
            algorithm=request.algorithm,
            mode=request.mode,
            operation=request.operation,
            language=request.language,
            test_feedback=request.test_feedback
        )
        
        return JSONResponse({
            'success': True,
            'code': improved_code,
            'message': '代码改进成功，请重新测试'
        })
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"代码改进失败: {error_msg}")
        return JSONResponse(
            {
                'success': False,
                'error': error_msg
            },
            status_code=500
        )


@app.post("/api/compare-code")
async def compare_code(request: CompareCodeRequest):
    """对比两个代码"""
    try:
        # 从历史记录或存储中获取代码（这里简化处理，实际应该从数据库或存储中获取）
        # 暂时返回模拟数据，实际实现需要存储代码历史
        return JSONResponse({
            'success': False,
            'error': '代码对比功能需要先实现代码历史存储功能'
        })
    except Exception as e:
        logger.error(f"代码对比失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.post("/api/analyze-performance")
async def analyze_performance(request: AnalyzePerformanceRequest):
    """分析代码性能"""
    try:
        import time
        import subprocess
        import tempfile
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py' if request.language == 'python' else '.c', delete=False) as f:
            f.write(request.code)
            temp_file = f.name
        
        times = []
        for _ in range(request.iterations):
            start = time.time()
            
            if request.language == 'python':
                result = subprocess.run(
                    ['python', temp_file],
                    input=str(request.data_size),
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            elif request.language == 'c':
                # 编译
                compile_result = subprocess.run(
                    ['gcc', '-o', temp_file + '.exe', temp_file, '-lm'],
                    capture_output=True,
                    timeout=10
                )
                if compile_result.returncode == 0:
                    # 运行
                    result = subprocess.run(
                        [temp_file + '.exe'],
                        input=str(request.data_size),
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                else:
                    raise Exception(f"编译失败: {compile_result.stderr}")
            
            end = time.time()
            times.append((end - start) * 1000)  # 转换为毫秒
        
        # 清理临时文件
        import os
        try:
            os.unlink(temp_file)
            if request.language == 'c' and os.path.exists(temp_file + '.exe'):
                os.unlink(temp_file + '.exe')
        except:
            pass
        
        metrics = {
            'avg_time': sum(times) / len(times),
            'min_time': min(times),
            'max_time': max(times),
            'times': times
        }
        
        return JSONResponse({
            'success': True,
            'metrics': metrics
        })
    except Exception as e:
        logger.error(f"性能分析失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.post("/api/explain-code")
async def explain_code(request: ExplainCodeRequest):
    """
    使用AI解释代码 - 支持多页面并发执行
    
    每个请求都会创建独立的Agent实例，支持真正的并发
    可以指定provider使用不同的LLM进行解释
    """
    try:
        # 为每个请求创建独立的Agent实例，支持真正的并发
        # 如果指定了provider，使用指定的；否则使用默认值
        agent = CryptoAgent(
            provider=request.provider,
            enable_validation=False
        )
        
        # 构建解释提示词
        depth_map = {
            'simple': '简单',
            'detailed': '详细',
            'expert': '专家级'
        }
        
        prompt = f"请用{depth_map.get(request.depth, '详细')}的方式解释以下{request.language.upper()}代码的工作原理：\n\n"
        prompt += f"算法：{request.algorithm or '未知'}\n"
        if request.mode:
            prompt += f"模式：{request.mode}\n"
        prompt += f"\n代码：\n```{request.language}\n{request.code}\n```\n\n"
        prompt += "请详细解释：\n1. 代码的整体功能\n2. 关键函数的作用\n3. 算法实现原理\n4. 重要参数的含义\n5. 代码的执行流程"
        
        system_prompt = "你是一位专业的密码学和编程专家，擅长解释代码的工作原理。"
        
        explanation = await agent.llm.generate(prompt, system_prompt)
        
        return JSONResponse({
            'success': True,
            'explanation': explanation
        })
    except Exception as e:
        logger.error(f"代码解释失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.post("/api/batch-generate")
async def batch_generate(request: BatchGenerateRequest):
    """
    批量生成代码（并行执行多个LLM请求）
    
    支持真正的并发执行，每个配置都会创建独立的Agent实例
    多个页面可以同时调用此接口，互不干扰
    """
    try:
        async def _run_row(i: int) -> Dict[str, Any]:
            return await _batch_generate_single(request.provider, request.configs[i])

        results = await asyncio.gather(
            *[_run_row(i) for i in range(len(request.configs))],
            return_exceptions=True,
        )

        processed_results: List[Dict[str, Any]] = []
        for i, result in enumerate(results):
            cfg = request.configs[i]
            if isinstance(result, Exception):
                processed_results.append(
                    _batch_result_pack(
                        cfg,
                        success=False,
                        generation_ok=False,
                        vector_status="—",
                        vector_detail="",
                        error=str(result),
                        code=None,
                        filename=None,
                        total_ms=0,
                    )
                )
            else:
                processed_results.append(result)

        return JSONResponse({"success": True, "results": processed_results})
    except Exception as e:
        logger.error(f"批量生成失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/batch-generate-stream")
async def batch_generate_stream(request: BatchGenerateRequest):
    """批量生成：SSE 实时进度，最后一条事件携带完整 results 与成功/失败统计。"""

    async def event_gen():
        n = len(request.configs)
        if n == 0:
            yield f"data: {json.dumps({'type': 'error', 'message': '请至少选择一个配置'}, ensure_ascii=False)}\n\n"
            return

        slots: List[Optional[Dict[str, Any]]] = [None] * n

        async def indexed(i: int):
            r = await _batch_generate_single(request.provider, request.configs[i])
            return i, r

        def _progress_done_dict(i: int, r: Dict[str, Any]) -> Dict[str, Any]:
            cfg = request.configs[i]
            ok = bool(r.get("success"))
            code = r.get("code")
            stream_code = None
            if ok and _BATCH_SSE_PROGRESS_INCLUDE_CODE:
                max_stream_code = 400_000
                stream_code = code if isinstance(code, str) and len(code) <= max_stream_code else None
            return {
                "type": "progress",
                "phase": "done",
                "completed": i + 1,
                "total": n,
                "index": i,
                "label": _batch_stream_label(cfg),
                "success": ok,
                "error": r.get("error"),
                "provider_line": llm_provider_display_name(request.provider),
                "algorithm": cfg.get("algorithm", ""),
                "mode": (cfg.get("mode") or "") or "—",
                "language": cfg.get("language", ""),
                "case_id": r.get("case_id", _batch_case_id_from_config(cfg)),
                "gen": "成功" if r.get("generation_ok") else "失败",
                "vector": r.get("vector_display", "—"),
                "vector_detail": r.get("vector_detail", ""),
                "total_ms": r.get("total_ms", 0),
                "has_code": bool(ok and code),
                "code": stream_code,
                "row_crypt_missing": False,
            }

        try:
            # 本地 Ollama：单项含生成+Self-Refine+测试可能十余分钟，必须在「开始该项」时就推送 SSE，
            # 否则网页整段停在 0/N，用户误以为卡住（终端此时已有推理日志）。
            if _provider_is_local_batch(request.provider):
                for i in range(n):
                    cfg = request.configs[i]
                    start_ev = {
                        "type": "progress",
                        "phase": "running",
                        "completed": i,
                        "total": n,
                        "index": i,
                        "label": _batch_stream_label(cfg),
                        "success": False,
                        "error": None,
                        "provider_line": llm_provider_display_name(request.provider),
                        "algorithm": cfg.get("algorithm", ""),
                        "mode": (cfg.get("mode") or "") or "—",
                        "language": cfg.get("language", ""),
                        "case_id": _batch_case_id_from_config(cfg),
                        "gen": "运行中",
                        "vector": "—",
                        "vector_detail": "",
                        "total_ms": 0,
                        "has_code": False,
                        "code": None,
                        "row_crypt_missing": False,
                    }
                    yield f"data: {json.dumps(start_ev, ensure_ascii=False)}\n\n"

                    # 单项可能长达数十分钟；周期性发送 SSE 注释帧（: ping），减轻 nginx/浏览器对流的缓冲，
                    # 避免「终端已有日志但页面长期停在 0/N」的观感。
                    _item_task = asyncio.create_task(
                        _batch_generate_single(request.provider, cfg)
                    )
                    while not _item_task.done():
                        await asyncio.wait([_item_task], timeout=15.0)
                        if _item_task.done():
                            break
                        yield ": ping\n\n"
                    r = _item_task.result()
                    slots[i] = r
                    yield f"data: {json.dumps(_progress_done_dict(i, r), ensure_ascii=False)}\n\n"
            else:
                tasks = [asyncio.create_task(indexed(i)) for i in range(n)]
                completed = 0
                for fut in asyncio.as_completed(tasks):
                    i, r = await fut
                    slots[i] = r
                    completed += 1
                    cfg = request.configs[i]
                    code = r.get("code")
                    stream_code = None
                    if _BATCH_SSE_PROGRESS_INCLUDE_CODE:
                        max_stream_code = 400_000
                        stream_code = code if isinstance(code, str) and len(code) <= max_stream_code else None
                    progress = {
                        "type": "progress",
                        "phase": "done",
                        "completed": completed,
                        "total": n,
                        "index": i,
                        "label": _batch_stream_label(cfg),
                        "success": bool(r.get("success")),
                        "error": r.get("error"),
                        "provider_line": llm_provider_display_name(request.provider),
                        "algorithm": cfg.get("algorithm", ""),
                        "mode": (cfg.get("mode") or "") or "—",
                        "language": cfg.get("language", ""),
                        "case_id": r.get("case_id", _batch_case_id_from_config(cfg)),
                        "gen": "成功" if r.get("generation_ok") else "失败",
                        "vector": r.get("vector_display", "—"),
                        "vector_detail": r.get("vector_detail", ""),
                        "total_ms": r.get("total_ms", 0),
                        "has_code": bool(code),
                        "code": stream_code,
                        "row_crypt_missing": False,
                    }
                    yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"批量流式生成异常: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            return

        success_count = sum(1 for x in slots if x and x.get("success"))
        fail_count = n - success_count
        generation_ok_count = sum(1 for x in slots if x and x.get("generation_ok"))
        final = {
            "type": "complete",
            "results": slots,
            "success_count": success_count,
            "fail_count": fail_count,
            "generation_ok_count": generation_ok_count,
            "total": n,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/security-scan")
async def security_scan(request: SecurityScanRequest):
    """扫描代码安全漏洞"""
    try:
        issues = []
        code_lower = request.code.lower()
        
        # 检查常见安全问题
        security_patterns = {
            'critical': [
                ('硬编码密钥', ['password', 'secret', 'key', 'api_key'], '避免在代码中硬编码密钥，应使用环境变量或配置文件'),
                ('弱随机数', ['rand()', 'random()', 'srand('], '使用弱随机数生成器可能导致安全问题，应使用加密安全的随机数生成器'),
                ('缓冲区溢出风险', ['gets(', 'strcpy(', 'sprintf('], '这些函数容易导致缓冲区溢出，应使用更安全的替代函数'),
            ],
            'warning': [
                ('未验证输入', ['input(', 'scanf(', 'gets('], '应验证所有用户输入，防止注入攻击'),
                ('敏感信息输出', ['print', 'printf', 'console.log'], '避免在代码中输出敏感信息'),
                ('不安全的加密', ['md5', 'sha1'], 'MD5和SHA1已被认为不安全，应使用SHA-256或更强的算法'),
            ],
            'info': [
                ('缺少错误处理', ['try:', 'except:', 'catch'], '建议添加完善的错误处理机制'),
                ('缺少注释', ['#', '//', '/*'], '建议添加必要的代码注释'),
            ]
        }
        
        for severity, patterns in security_patterns.items():
            for issue_name, keywords, suggestion in patterns:
                found = any(keyword in code_lower for keyword in keywords)
                if found:
                    issues.append({
                        'severity': severity,
                        'message': f'发现潜在安全问题: {issue_name}',
                        'suggestion': suggestion
                    })
        
        # 语言特定检查
        if request.language == 'c':
            if 'malloc' in code_lower and 'free' in code_lower:
                # 检查内存泄漏风险
                malloc_count = code_lower.count('malloc')
                free_count = code_lower.count('free')
                if malloc_count > free_count:
                    issues.append({
                        'severity': 'warning',
                        'message': '可能存在内存泄漏：malloc调用次数多于free调用次数',
                        'suggestion': '确保每个malloc都有对应的free调用'
                    })
        
        return JSONResponse({
            'success': True,
            'issues': issues
        })
    except Exception as e:
        logger.error(f"安全扫描失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


class OpenSSLTestRequest(BaseModel):
    """OpenSSL测试请求模型"""
    algorithm: str
    mode: Optional[str] = None
    plaintext: str
    key: Optional[str] = None
    iv: Optional[str] = None
    key_size: Optional[int] = None
    # RSA相关参数
    public_key_n: Optional[str] = None  # 公钥模数n
    public_key_e: Optional[str] = None   # 公钥指数e
    private_key_n: Optional[str] = None  # 私钥模数n
    private_key_d: Optional[str] = None  # 私钥指数d
    expected_signature: Optional[str] = None  # 预期数字签名


class OpenSSLCompareRequest(BaseModel):
    """OpenSSL对比请求模型"""
    generated_ciphertext: str
    plaintext: str
    key: str
    iv: Optional[str] = None
    algorithm: str
    mode: Optional[str] = None
    key_size: Optional[int] = None


@app.get("/api/openssl-check")
async def check_openssl():
    """检查OpenSSL是否可用"""
    try:
        tester = OpenSSLTester()
        return JSONResponse({
            'success': True,
            'available': tester.is_available()
        })
    except Exception as e:
        logger.error(f"检查OpenSSL状态失败: {e}")
        return JSONResponse({
            'success': False,
            'available': False,
            'error': str(e)
        })


@app.post("/api/openssl-test")
async def openssl_test(request: OpenSSLTestRequest):
    """使用OpenSSL进行标准测试"""
    try:
        tester = OpenSSLTester()
        
        if not tester.is_available():
            return JSONResponse({
                'success': False,
                'error': 'OpenSSL未安装或不在PATH中'
            }, status_code=400)
        
        # 处理RSA算法
        if request.algorithm.upper() == 'RSA':
            # RSA测试：生成签名并验证
            try:
                # 使用OpenSSL生成RSA签名
                success, message, details = tester.test_rsa_sign(
                    plaintext_hex=request.plaintext,
                    private_key_n=request.private_key_n,
                    private_key_d=request.private_key_d
                )
                
                if success and details.get('signature'):
                    signature = details['signature']
                    details['signature'] = signature
                    
                    # 如果提供了预期签名，进行验证
                    if request.expected_signature:
                        expected_sig = request.expected_signature.replace(' ', '').replace('\n', '').strip().lower()
                        actual_sig = signature.replace(' ', '').replace('\n', '').strip().lower()
                        signature_match = (expected_sig == actual_sig)
                        details['signature_match'] = signature_match
                        details['expected_signature'] = request.expected_signature
                        
                        if signature_match:
                            message += " 签名验证通过"
                        else:
                            message += " 签名验证失败"
                
                return JSONResponse({
                    'success': success,
                    'message': message,
                    'details': details
                })
            except Exception as e:
                logger.error(f"RSA测试失败: {e}")
                return JSONResponse({
                    'success': False,
                    'error': f'RSA测试失败: {str(e)}'
                }, status_code=500)
        
        # 处理对称加密算法
        success, message, details = tester.test_encrypt(
            algorithm=request.algorithm,
            plaintext_hex=request.plaintext,
            key_hex=request.key,
            iv_hex=request.iv,
            mode=request.mode,
            key_size=request.key_size
        )
        
        return JSONResponse({
            'success': success,
            'message': message,
            'details': details
        })
    except Exception as e:
        logger.error(f"OpenSSL测试失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.post("/api/openssl-compare")
async def openssl_compare(request: OpenSSLCompareRequest):
    """将生成的密文与OpenSSL标准结果进行比较"""
    try:
        tester = OpenSSLTester()
        
        if not tester.is_available():
            return JSONResponse({
                'success': False,
                'error': 'OpenSSL未安装或不在PATH中'
            }, status_code=400)
        
        success, message, details = tester.compare_with_openssl(
            generated_ciphertext=request.generated_ciphertext,
            plaintext_hex=request.plaintext,
            key_hex=request.key,
            iv_hex=request.iv,
            algorithm=request.algorithm,
            mode=request.mode,
            key_size=request.key_size
        )
        
        return JSONResponse({
            'success': success,
            'message': message,
            'details': details
        })
    except Exception as e:
        logger.error(f"OpenSSL对比失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/openssl-code/tree")
async def get_openssl_code_tree():
    """获取OpenSSL源代码文件树"""
    try:
        openssl_dir = Path(__file__).parent.parent / "openssl"
        
        if not openssl_dir.exists():
            return JSONResponse({
                'success': False,
                'error': 'OpenSSL目录不存在，请先克隆OpenSSL仓库'
            }, status_code=404)
        
        # 主要算法目录
        main_dirs = [
            'crypto/aes',
            'crypto/des',
            'crypto/evp',
            'crypto/modes',
            'crypto/rsa',
            'crypto/sm4' if (openssl_dir / 'crypto/sm4').exists() else None
        ]
        
        def build_tree(directory: Path, relative_path: str = ""):
            """递归构建文件树"""
            tree = []
            
            if not directory.exists() or not directory.is_dir():
                return tree
            
            try:
                items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
                
                for item in items:
                    # 跳过隐藏文件和特殊目录
                    if item.name.startswith('.') or item.name in ['__pycache__', 'node_modules']:
                        continue
                    
                    # 限制深度，避免加载过多文件
                    depth = relative_path.count('/') if relative_path else 0
                    if depth > 5:  # 最多5层深度
                        continue
                    
                    # 限制文件大小（只显示小于1MB的文件）
                    if item.is_file() and item.stat().st_size > 1024 * 1024:
                        continue
                    
                    current_path = f"{relative_path}/{item.name}" if relative_path else item.name
                    
                    if item.is_dir():
                        # 递归处理子目录
                        children = build_tree(item, current_path)
                        if children:  # 只添加非空目录
                            tree.append({
                                'name': item.name,
                                'type': 'directory',
                                'path': current_path,
                                'children': children
                            })
                    else:
                        # 只添加源代码文件
                        if item.suffix in ['.c', '.h', '.cpp', '.hpp', '.py', '.md', '.yaml', '.yml']:
                            tree.append({
                                'name': item.name,
                                'type': 'file',
                                'path': current_path
                            })
            except PermissionError:
                logger.warning(f"无权限访问目录: {directory}")
            except Exception as e:
                logger.warning(f"读取目录失败 {directory}: {e}")
            
            return tree
        
        # 构建主要目录的树结构
        result_tree = []
        for main_dir in main_dirs:
            if main_dir is None:
                continue
            dir_path = openssl_dir / main_dir
            if dir_path.exists():
                children = build_tree(dir_path, main_dir)
                if children:
                    result_tree.append({
                        'name': main_dir.split('/')[-1],
                        'type': 'directory',
                        'path': main_dir,
                        'children': children
                    })
        
        return JSONResponse({
            'success': True,
            'tree': result_tree
        })
    except Exception as e:
        logger.error(f"获取OpenSSL代码树失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@app.get("/api/openssl-code/file")
async def get_openssl_code_file(path: str = Query(..., description="文件路径")):
    """获取OpenSSL源代码文件内容"""
    try:
        openssl_dir = Path(__file__).parent.parent / "openssl"
        file_path = openssl_dir / path
        
        # 安全检查：确保文件在openssl目录内
        try:
            file_path.resolve().relative_to(openssl_dir.resolve())
        except ValueError:
            return JSONResponse({
                'success': False,
                'error': '无效的文件路径'
            }, status_code=400)
        
        if not file_path.exists():
            return JSONResponse({
                'success': False,
                'error': '文件不存在'
            }, status_code=404)
        
        if not file_path.is_file():
            return JSONResponse({
                'success': False,
                'error': '路径不是文件'
            }, status_code=400)
        
        # 限制文件大小（最大5MB）
        if file_path.stat().st_size > 5 * 1024 * 1024:
            return JSONResponse({
                'success': False,
                'error': '文件过大（超过5MB）'
            }, status_code=400)
        
        # 读取文件内容
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except UnicodeDecodeError:
            # 如果UTF-8解码失败，尝试其他编码
            with open(file_path, 'r', encoding='latin-1', errors='ignore') as f:
                content = f.read()
        
        return JSONResponse({
            'success': True,
            'content': content,
            'path': path,
            'size': file_path.stat().st_size
        })
    except Exception as e:
        logger.error(f"读取OpenSSL文件失败: {e}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    import multiprocessing
    
    # 获取CPU核心数，用于设置worker数量
    cpu_count = multiprocessing.cpu_count()
    workers = max(1, min(cpu_count, 4))  # 最多4个worker，至少1个
    
    logger.info(f"启动服务器，使用 {workers} 个worker进程（CPU核心数: {cpu_count}）")
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        workers=workers,  # 多进程支持
        log_level="info"
    )

