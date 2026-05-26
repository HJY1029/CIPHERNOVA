"""
对 LLM 常见疏漏做**确定性**小修补（不调用模型），便于与 CodeTester 联调或离线自检。

当前仅处理 Python：缺 ``import os``、``Crypto.Cipher``（DES/AES）、``binascii``、
``Crypto.Util.Padding``、``Crypto.Random`` 等常见疏漏。
"""
from __future__ import annotations

import re
from typing import List


def _has_line_import_os(code: str) -> bool:
    return bool(re.search(r"(?m)^\s*import os\s*(#.*)?$", code)) or bool(
        re.search(r"(?m)^\s*from os\s+import\s+", code)
    )


def _has_import_binascii(code: str) -> bool:
    return bool(re.search(r"(?m)^\s*import binascii\s", code))


def _has_crypto_des(code: str) -> bool:
    return "from Crypto.Cipher import DES" in code or "from Cryptodome.Cipher import DES" in code


def _has_crypto_aes(code: str) -> bool:
    return "from Crypto.Cipher import AES" in code or "from Cryptodome.Cipher import AES" in code


def _has_padding_util(code: str) -> bool:
    return "Crypto.Util.Padding" in code or "Cryptodome.Util.Padding" in code


def _has_crypto_random(code: str) -> bool:
    return bool(re.search(r"(?m)^\s*from Crypto\.Random import\b", code)) or bool(
        re.search(r"(?m)^\s*from Cryptodome\.Random import\b", code)
    )


def apply_common_quickfixes(code: str, language: str) -> str:
    """
    在**不改变算法逻辑**的前提下补全典型缺失 import。
    多次调用应稳定（已存在则不重复插入）。
    """
    lang = (language or "").strip().lower()
    if lang != "python":
        return code

    prefixes: List[str] = []

    if (re.search(r"\bos\.environ\b", code) or re.search(r"\bos\.getenv\b", code)) and not _has_line_import_os(
        code
    ):
        prefixes.append("import os\n")

    if re.search(r"\bDES\.", code) and not _has_crypto_des(code):
        prefixes.append("from Crypto.Cipher import DES\n")

    if re.search(r"\bAES\.", code) and not _has_crypto_aes(code):
        prefixes.append("from Crypto.Cipher import AES\n")

    if re.search(r"\bbinascii\.", code) and not _has_import_binascii(code):
        prefixes.append("import binascii\n")

    if (re.search(r"\bpad\s*\(", code) or re.search(r"\bunpad\s*\(", code)) and not _has_padding_util(code):
        prefixes.append("from Crypto.Util.Padding import pad, unpad\n")

    if re.search(r"\bget_random_bytes\s*\(", code) and not _has_crypto_random(code):
        prefixes.append("from Crypto.Random import get_random_bytes\n")

    if not prefixes:
        return code
    return "".join(prefixes) + code
