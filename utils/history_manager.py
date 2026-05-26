"""
历史记录管理器 - 使用SQLite数据库管理代码历史记录
"""
import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple
from utils.logger import setup_logger

logger = setup_logger()

class HistoryManager:
    """历史记录管理器 - 使用SQLite数据库"""
    
    def __init__(self, db_path: str = "code_history.db", history_file: Optional[str] = None):
        """
        初始化历史记录管理器
        
        Args:
            db_path: 数据库文件路径
            history_file: 旧的JSON历史记录文件路径（用于迁移）
        """
        self.db_path = Path(db_path)
        self.history_file = Path(history_file) if history_file else Path("code_history.json")
        self.lock = threading.Lock()
        self._init_database()
        
        # 如果存在旧的JSON文件且数据库为空，则迁移数据
        if self.history_file.exists() and self._is_database_empty():
            self._migrate_from_json()
    
    def _init_database(self):
        """初始化数据库表结构"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 创建历史记录表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS code_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        algorithm TEXT NOT NULL,
                        mode TEXT,
                        language TEXT NOT NULL,
                        code TEXT NOT NULL,
                        code_preview TEXT,
                        provider TEXT NOT NULL,
                        operation TEXT DEFAULT '加密解密',
                        validation_success INTEGER DEFAULT 1,
                        test_success INTEGER DEFAULT 1,
                        generation_time REAL DEFAULT 0.0,
                        attempts INTEGER DEFAULT 1,
                        filename TEXT,
                        extra_data TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 创建索引以提高查询性能
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_timestamp ON code_history(timestamp DESC)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_algorithm ON code_history(algorithm)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_language ON code_history(language)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_provider ON code_history(provider)
                """)
                
                conn.commit()
                logger.info(f"数据库初始化成功: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise
    
    def _is_database_empty(self) -> bool:
        """检查数据库是否为空"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM code_history")
                count = cursor.fetchone()[0]
                return count == 0
        except Exception as e:
            logger.error(f"检查数据库是否为空失败: {e}")
            return True
    
    def _migrate_from_json(self):
        """从JSON文件迁移数据到数据库"""
        try:
            logger.info("开始从JSON文件迁移数据到数据库...")
            
            # 读取JSON文件
            with open(self.history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            
            if not history:
                logger.info("JSON文件为空，无需迁移")
                return
            
            # 批量插入数据
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                for item in history:
                    # 提取extra_data（除了标准字段外的其他数据）
                    standard_fields = {
                        'id', 'timestamp', 'algorithm', 'mode', 'language', 
                        'code', 'code_preview', 'provider', 'operation',
                        'validation_success', 'test_success', 'generation_time',
                        'attempts', 'filename'
                    }
                    extra_data = {k: v for k, v in item.items() if k not in standard_fields}
                    
                    cursor.execute("""
                        INSERT INTO code_history (
                            timestamp, algorithm, mode, language, code, code_preview,
                            provider, operation, validation_success, test_success,
                            generation_time, attempts, filename, extra_data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        item.get('timestamp', datetime.now().isoformat()),
                        item.get('algorithm', ''),
                        item.get('mode'),
                        item.get('language', ''),
                        item.get('code', ''),
                        item.get('code_preview', ''),
                        item.get('provider', ''),
                        item.get('operation', '加密解密'),
                        1 if item.get('validation_success', True) else 0,
                        1 if item.get('test_success', True) else 0,
                        item.get('generation_time', 0.0),
                        item.get('attempts', 1),
                        item.get('filename'),
                        json.dumps(extra_data, ensure_ascii=False) if extra_data else None
                    ))
                
                conn.commit()
                logger.info(f"成功迁移 {len(history)} 条历史记录到数据库")
                
                # 备份JSON文件
                backup_file = self.history_file.with_suffix('.json.backup')
                if not backup_file.exists():
                    import shutil
                    shutil.copy2(self.history_file, backup_file)
                    logger.info(f"已备份JSON文件到: {backup_file}")
        except Exception as e:
            logger.error(f"从JSON迁移数据失败: {e}")
            raise
    
    def add_history(self, 
                   algorithm: str,
                   mode: Optional[str],
                   language: str,
                   code: str,
                   provider: str,
                   operation: str = "加密解密",
                   validation_success: bool = True,
                   test_success: bool = True,
                   generation_time: float = 0.0,
                   attempts: int = 1,
                   filename: Optional[str] = None,
                   **kwargs) -> Dict[str, Any]:
        """
        添加历史记录
        
        Args:
            algorithm: 算法名称
            mode: 模式
            language: 编程语言
            code: 代码内容
            provider: LLM提供商
            operation: 操作类型
            validation_success: 验证是否成功
            test_success: 测试是否成功
            generation_time: 生成时长（秒）
            attempts: 尝试次数
            filename: 文件名
            **kwargs: 其他信息写入 ``extra_data``（如 ``test_details``、``distillation_active``）
        
        Returns:
            历史记录项
        """
        with self.lock:
            try:
                timestamp = datetime.now().isoformat()
                code_preview = code[:500] + '...' if len(code) > 500 else code
                
                # 提取extra_data
                standard_fields = {
                    'algorithm', 'mode', 'language', 'code', 'provider',
                    'operation', 'validation_success', 'test_success',
                    'generation_time', 'attempts', 'filename'
                }
                extra_data = {k: v for k, v in kwargs.items() if k not in standard_fields}
                
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        INSERT INTO code_history (
                            timestamp, algorithm, mode, language, code, code_preview,
                            provider, operation, validation_success, test_success,
                            generation_time, attempts, filename, extra_data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        timestamp,
                        algorithm,
                        mode,
                        language,
                        code,
                        code_preview,
                        provider,
                        operation,
                        1 if validation_success else (0 if validation_success is False else None),
                        1 if test_success else (0 if test_success is False else None),
                        generation_time,
                        attempts,
                        filename,
                        json.dumps(extra_data, ensure_ascii=False) if extra_data else None
                    ))
                    
                    history_id = cursor.lastrowid
                    conn.commit()
                
                # 清理旧记录，只保留最近10000条
                self._cleanup_old_records(keep_count=10000)
                
                # 构建返回的历史记录项
                history_item = {
                    'id': str(history_id),
                    'timestamp': timestamp,
                    'algorithm': algorithm,
                    'mode': mode,
                    'language': language,
                    'code': code,
                    'code_preview': code_preview,
                    'provider': provider,
                    'operation': operation,
                    'validation_success': validation_success,
                    'test_success': test_success,
                    'generation_time': generation_time,
                    'attempts': attempts,
                    'filename': filename,
                    **extra_data
                }
                
                logger.info(f"已添加历史记录: {algorithm} {mode or ''} - {language} (ID: {history_id})")
                
                return history_item
            except Exception as e:
                logger.error(f"添加历史记录失败: {e}")
                raise
    
    def get_history(self, limit: Optional[int] = None, reverse: bool = True, 
                   algorithm: Optional[str] = None, language: Optional[str] = None,
                   provider: Optional[str] = None, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取历史记录
        
        Args:
            limit: 返回的最大记录数
            reverse: 是否倒序（最新的在前）
            algorithm: 按算法筛选
            language: 按语言筛选
            provider: 按提供商筛选
            mode: 按模式筛选；传入 "__none__" 时仅保留 mode 为空/NULL 的记录（如部分 RSA）
        
        Returns:
            历史记录列表
        """
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    # 构建查询条件
                    conditions = []
                    params = []
                    
                    if algorithm:
                        conditions.append("algorithm = ?")
                        params.append(algorithm)
                    
                    if language:
                        conditions.append("language = ?")
                        params.append(language)
                    
                    if provider:
                        conditions.append("provider = ?")
                        params.append(provider)

                    if mode is not None and mode != "":
                        if mode == "__none__":
                            conditions.append("(mode IS NULL OR TRIM(COALESCE(mode, '')) = '')")
                        else:
                            conditions.append("mode = ?")
                            params.append(mode)
                    
                    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
                    order_clause = " ORDER BY timestamp DESC" if reverse else " ORDER BY timestamp ASC"
                    limit_clause = f" LIMIT {limit}" if limit else ""
                    
                    query = f"SELECT * FROM code_history{where_clause}{order_clause}{limit_clause}"
                    cursor.execute(query, params)
                    
                    rows = cursor.fetchall()
                    
                    # 转换为字典列表
                    history = []
                    for row in rows:
                        item = dict(row)
                        # 转换整数字段
                        item['id'] = str(item['id'])
                        item['validation_success'] = bool(item['validation_success'])
                        item['test_success'] = bool(item['test_success'])
                        
                        # 解析extra_data
                        if item.get('extra_data'):
                            try:
                                extra = json.loads(item['extra_data'])
                                item.update(extra)
                            except:
                                pass
                        
                        # 移除extra_data字段（已合并到item中）
                        item.pop('extra_data', None)
                        item.pop('created_at', None)
                        
                        history.append(item)
                    
                    return history
            except Exception as e:
                logger.error(f"获取历史记录失败: {e}")
                return []

    @staticmethod
    def normalize_case_key(
        algorithm: Optional[str], mode: Optional[str], language: Optional[str]
    ) -> Tuple[str, str, str]:
        """与批量 config 对齐的 (algorithm, mode, language) 归一化键。"""
        a = (algorithm or "").strip().upper()
        m = (mode or "").strip().lower()
        lang = (language or "").strip().lower()
        return (a, m, lang)

    def get_latest_local_success_record_since(
        self,
        algorithm: Optional[str],
        mode: Optional[str],
        language: Optional[str],
        since: str,
    ) -> Optional[Dict[str, Any]]:
        """
        自 since（当天 00:00 起，与 get_local_success_case_keys_since 口径一致）以来，
        本地类 provider（名含 local / ollama）且 test_success=1 的**最新一条**记录（按 timestamp 降序）。
        用于批量任务对历史成功代码做向量复测。
        """
        since = (since or "").strip()[:10]
        if not since:
            return None
        na, nm, nl = self.normalize_case_key(algorithm, mode, language)
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT id, timestamp, code, filename, provider, algorithm, mode, language,
                               COALESCE(NULLIF(TRIM(operation), ''), '加密解密') AS operation
                        FROM code_history
                        WHERE test_success = 1
                          AND timestamp >= ?
                          AND (
                            instr(lower(COALESCE(provider, '')), 'local') > 0
                            OR instr(lower(COALESCE(provider, '')), 'ollama') > 0
                          )
                          AND upper(trim(algorithm)) = ?
                          AND lower(trim(COALESCE(mode, ''))) = ?
                          AND lower(trim(language)) = ?
                        ORDER BY timestamp DESC
                        LIMIT 1
                        """,
                        (since, na, nm, nl),
                    )
                    row = cursor.fetchone()
                    if not row:
                        return None
                    item = dict(row)
                    item["id"] = str(item["id"])
                    item["test_success"] = True
                    return item
            except Exception as e:
                logger.error(f"查询最新本地成功记录失败: {e}")
                return None

    def get_latest_success_record_since_for_provider(
        self,
        algorithm: Optional[str],
        mode: Optional[str],
        language: Optional[str],
        since: str,
        provider: str,
    ) -> Optional[Dict[str, Any]]:
        """
        自 since（当天 00:00 起）以来，指定 **provider**（精确匹配，忽略大小写）且 ``test_success=1``
        的最新一条记录。用于云端等非本地线路批量任务：仅当该 provider 在该槽位已有成功落库时才可跳过再生成。
        """
        since = (since or "").strip()[:10]
        pv = (provider or "").strip().lower()
        if not since or not pv:
            return None
        na, nm, nl = self.normalize_case_key(algorithm, mode, language)
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT id, timestamp, code, filename, provider, algorithm, mode, language,
                               COALESCE(NULLIF(TRIM(operation), ''), '加密解密') AS operation
                        FROM code_history
                        WHERE test_success = 1
                          AND timestamp >= ?
                          AND lower(trim(COALESCE(provider, ''))) = ?
                          AND upper(trim(algorithm)) = ?
                          AND lower(trim(COALESCE(mode, ''))) = ?
                          AND lower(trim(language)) = ?
                        ORDER BY timestamp DESC
                        LIMIT 1
                        """,
                        (since, pv, na, nm, nl),
                    )
                    row = cursor.fetchone()
                    if not row:
                        return None
                    item = dict(row)
                    item["id"] = str(item["id"])
                    item["test_success"] = True
                    return item
            except Exception as e:
                logger.error(f"查询指定 provider 最新成功记录失败: {e}")
                return None

    def refresh_history_timestamp(self, history_id: str) -> bool:
        """复测通过后刷新记录时间戳，使 local_batch_skip_if_success_since 仍命中该条。"""
        if not history_id:
            return False
        try:
            hid = int(history_id)
        except (TypeError, ValueError):
            return False
        ts = datetime.now().isoformat()
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE code_history SET timestamp = ? WHERE id = ?",
                        (ts, hid),
                    )
                    conn.commit()
                    if cursor.rowcount > 0:
                        logger.info(f"已刷新历史记录时间戳: id={history_id}")
                        return True
            except Exception as e:
                logger.error(f"刷新历史时间戳失败: {e}")
        return False

    def get_local_success_case_keys_since(self, since: str) -> Set[Tuple[str, str, str]]:
        """
        自 since 当天 00:00 起（timestamp >= since，字符串比较兼容 ISO 时间戳），
        历史库中 provider 为本地类（名含 local / ollama）且 test_success=1 的去重
        (algorithm, mode, language) 键集合，供本地批量跳过已达成任务。
        since 示例: '2026-05-04'
        """
        since = (since or "").strip()[:10]
        if not since:
            return set()
        out: Set[Tuple[str, str, str]] = set()
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT DISTINCT algorithm, mode, language
                        FROM code_history
                        WHERE test_success = 1
                          AND timestamp >= ?
                          AND (
                            instr(lower(COALESCE(provider, '')), 'local') > 0
                            OR instr(lower(COALESCE(provider, '')), 'ollama') > 0
                          )
                        """,
                        (since,),
                    )
                    for algorithm, mode, language in cursor.fetchall():
                        out.add(self.normalize_case_key(algorithm, mode, language))
                return out
            except Exception as e:
                logger.error(f"查询本地成功用例键失败: {e}")
                return set()

    def update_history(self, history_id: str, **kwargs) -> bool:
        """
        更新历史记录
        
        Args:
            history_id: 历史记录ID
            **kwargs: 要更新的字段（validation_success, test_success, generation_time, attempts, test_details）
        
        Returns:
            是否更新成功
        """
        with self.lock:
            try:
                # 构建更新语句
                update_fields = []
                update_values = []
                
                if 'validation_success' in kwargs:
                    update_fields.append("validation_success = ?")
                    val = kwargs['validation_success']
                    update_values.append(1 if val else (0 if val is False else None))
                
                if 'test_success' in kwargs:
                    update_fields.append("test_success = ?")
                    val = kwargs['test_success']
                    update_values.append(1 if val else (0 if val is False else None))
                
                if 'generation_time' in kwargs:
                    update_fields.append("generation_time = ?")
                    update_values.append(kwargs['generation_time'])
                
                if 'attempts' in kwargs:
                    update_fields.append("attempts = ?")
                    update_values.append(kwargs['attempts'])
                
                if 'test_details' in kwargs:
                    # 更新extra_data中的test_details
                    # 先读取现有的extra_data
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT extra_data FROM code_history WHERE id = ?", (history_id,))
                        row = cursor.fetchone()
                        if row:
                            existing_extra = json.loads(row[0]) if row[0] else {}
                        else:
                            return False
                        existing_extra['test_details'] = kwargs['test_details']
                        update_fields.append("extra_data = ?")
                        update_values.append(json.dumps(existing_extra, ensure_ascii=False))
                
                if not update_fields:
                    return False
                
                update_values.append(history_id)
                
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        UPDATE code_history 
                        SET {', '.join(update_fields)}
                        WHERE id = ?
                    """, update_values)
                    conn.commit()
                    
                    if cursor.rowcount > 0:
                        logger.info(f"已更新历史记录 ID: {history_id}")
                        return True
                    else:
                        logger.warning(f"未找到历史记录 ID: {history_id}")
                        return False
            except Exception as e:
                logger.error(f"更新历史记录失败: {e}")
                return False
    
    def get_history_by_id(self, history_id: str) -> Optional[Dict[str, Any]]:
        """
        根据ID获取历史记录
        
        Args:
            history_id: 历史记录ID
        
        Returns:
            历史记录项，如果不存在则返回None
        """
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    cursor.execute("SELECT * FROM code_history WHERE id = ?", (history_id,))
                    row = cursor.fetchone()
                    
                    if row:
                        item = dict(row)
                        # 转换整数字段
                        item['id'] = str(item['id'])
                        item['validation_success'] = bool(item['validation_success'])
                        item['test_success'] = bool(item['test_success'])
                        
                        # 解析extra_data
                        if item.get('extra_data'):
                            try:
                                extra = json.loads(item['extra_data'])
                                item.update(extra)
                            except:
                                pass
                        
                        # 移除extra_data字段（已合并到item中）
                        item.pop('extra_data', None)
                        item.pop('created_at', None)
                        
                        return item
                    else:
                        return None
            except Exception as e:
                logger.error(f"获取历史记录项失败: {e}")
                return None
    
    def delete_history(self, history_id: str) -> bool:
        """
        删除历史记录
        
        Args:
            history_id: 历史记录ID
        
        Returns:
            是否删除成功
        """
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    cursor.execute("DELETE FROM code_history WHERE id = ?", (history_id,))
                    conn.commit()
                    
                    deleted = cursor.rowcount > 0
                    
                    if deleted:
                        logger.info(f"已删除历史记录: {history_id}")
                    else:
                        logger.warning(f"历史记录不存在: {history_id}")
                    
                    return deleted
            except Exception as e:
                logger.error(f"删除历史记录失败: {e}")
                return False

    def delete_history_by_ids(self, history_ids: List[int]) -> int:
        """批量删除历史记录；返回实际删除条数。"""
        ids = [int(i) for i in history_ids if i is not None]
        if not ids:
            return 0
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    placeholders = ",".join("?" * len(ids))
                    cursor.execute(
                        f"DELETE FROM code_history WHERE id IN ({placeholders})",
                        ids,
                    )
                    conn.commit()
                    n = int(cursor.rowcount or 0)
                    if n:
                        logger.info(f"已批量删除历史记录 {n} 条")
                    return n
            except Exception as e:
                logger.error(f"批量删除历史记录失败: {e}")
                return 0
    
    def clear_history(self) -> int:
        """
        清空所有历史记录
        
        Returns:
            删除的记录数
        """
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    cursor.execute("SELECT COUNT(*) FROM code_history")
                    count = cursor.fetchone()[0]
                    
                    cursor.execute("DELETE FROM code_history")
                    conn.commit()
                    
                    logger.info(f"已清空所有历史记录: {count} 条")
                    
                    return count
            except Exception as e:
                logger.error(f"清空历史记录失败: {e}")
                return 0
    
    def _cleanup_old_records(self, keep_count: int = 10000):
        """清理旧记录，只保留最近的N条"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 获取当前记录数
                cursor.execute("SELECT COUNT(*) FROM code_history")
                count = cursor.fetchone()[0]
                
                if count > keep_count:
                    # 删除最旧的记录
                    cursor.execute("""
                        DELETE FROM code_history 
                        WHERE id NOT IN (
                            SELECT id FROM code_history 
                            ORDER BY timestamp DESC 
                            LIMIT ?
                        )
                    """, (keep_count,))
                    conn.commit()
                    
                    deleted = count - keep_count
                    logger.info(f"已清理 {deleted} 条旧历史记录，保留最近 {keep_count} 条")
        except Exception as e:
            logger.warning(f"清理旧记录失败: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取历史记录统计信息
        
        Returns:
            统计信息字典
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 总记录数
                cursor.execute("SELECT COUNT(*) FROM code_history")
                total_count = cursor.fetchone()[0]
                
                # 按算法统计
                cursor.execute("""
                    SELECT algorithm, COUNT(*) as count 
                    FROM code_history 
                    GROUP BY algorithm 
                    ORDER BY count DESC
                """)
                by_algorithm = {row[0]: row[1] for row in cursor.fetchall()}
                
                # 按语言统计
                cursor.execute("""
                    SELECT language, COUNT(*) as count 
                    FROM code_history 
                    GROUP BY language 
                    ORDER BY count DESC
                """)
                by_language = {row[0]: row[1] for row in cursor.fetchall()}
                
                # 按提供商统计
                cursor.execute("""
                    SELECT provider, COUNT(*) as count 
                    FROM code_history 
                    GROUP BY provider 
                    ORDER BY count DESC
                """)
                by_provider = {row[0]: row[1] for row in cursor.fetchall()}

                # 按模式统计（NULL 与空串合并为 '' 便于前端「无模式」）
                cursor.execute("""
                    SELECT IFNULL(NULLIF(TRIM(mode), ''), ''), COUNT(*) as cnt
                    FROM code_history
                    GROUP BY IFNULL(NULLIF(TRIM(mode), ''), '')
                    ORDER BY cnt DESC
                """)
                by_mode = {row[0]: row[1] for row in cursor.fetchall()}
                
                # 验证成功率
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(validation_success) as success_count
                    FROM code_history
                """)
                row = cursor.fetchone()
                validation_rate = (row[1] / row[0] * 100) if row[0] > 0 else 0
                
                # 测试成功率
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(test_success) as success_count
                    FROM code_history
                """)
                row = cursor.fetchone()
                test_rate = (row[1] / row[0] * 100) if row[0] > 0 else 0
                
                return {
                    'total_count': total_count,
                    'by_algorithm': by_algorithm,
                    'by_language': by_language,
                    'by_provider': by_provider,
                    'by_mode': by_mode,
                    'validation_success_rate': round(validation_rate, 2),
                    'test_success_rate': round(test_rate, 2)
                }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {}
