#!/usr/bin/env python3
"""
离线自检：模拟 Web 上常见「缺 import」类失败样例 → ``apply_common_quickfixes`` → ``CodeTester`` 向量通过。

不调用 LLM。运行：
  python experiments/repair_roundtrip_selfcheck.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.code_tester import CodeTester  # noqa: E402
from utils.llm_code_quickfix import apply_common_quickfixes  # noqa: E402
from utils.test_data_loader import TestDataLoader  # noqa: E402

# --- 故障样例：刻意漏写 import，逻辑与 test_data.yaml 向量一致 ---

_BROKEN_DES_OFB_PY = r'''import binascii
from Crypto.Cipher import DES

def main():
    plaintext_hex = os.environ.get("TEST_PLAINTEXT")
    key_hex = os.environ.get("TEST_KEY")
    iv_hex = os.environ.get("TEST_IV")
    pt = binascii.unhexlify(plaintext_hex)
    key = binascii.unhexlify(key_hex)
    iv = binascii.unhexlify(iv_hex)
    cipher = DES.new(key, DES.MODE_OFB, iv=iv)
    ct = cipher.encrypt(pt)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''

_BROKEN_DES_CFB_PY = r'''import os
import binascii

def encrypt_des_cfb(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = DES.new(key, DES.MODE_CFB, iv=iv, segment_size=8)
    return cipher.encrypt(plaintext)

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    iv = binascii.unhexlify(os.environ["TEST_IV"])
    ct = encrypt_des_cfb(pt, key, iv)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''

_BROKEN_DES_ECB_PY = r'''import os
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    raw = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    pt = pad(raw, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(pt)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''


def _vec(algorithm: str, mode: str | None):
    td = TestDataLoader()
    d = td.get_test_data(algorithm, mode)
    assert d, f"无测试数据 {algorithm} {mode}"
    exp = d.get("expected_ciphertext")
    assert exp, d
    return {
        "plaintext": d["plaintext"],
        "expected_ciphertext": exp,
        "key": d["key"],
        "iv": d.get("iv"),
    }


class RepairRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.tester = CodeTester()

    def _assert_encrypt_ok(self, code: str, algorithm: str, mode: str):
        v = _vec(algorithm, mode)
        ok, msg, _ = self.tester.test(
            code,
            "python",
            plaintext=v["plaintext"],
            expected_ciphertext=v["expected_ciphertext"],
            key=v["key"],
            iv=v.get("iv"),
            algorithm=algorithm,
            mode=mode,
        )
        self.assertTrue(ok, msg)

    def test_des_ofb_missing_os_fixed_passes(self):
        fixed = apply_common_quickfixes(_BROKEN_DES_OFB_PY, "python")
        self.assertIn("import os", fixed)
        self._assert_encrypt_ok(fixed, "DES", "OFB")

    def test_des_cfb_missing_cipher_import_fixed_passes(self):
        fixed = apply_common_quickfixes(_BROKEN_DES_CFB_PY, "python")
        self.assertIn("from Crypto.Cipher import DES", fixed)
        self._assert_encrypt_ok(fixed, "DES", "CFB")

    def test_des_ecb_missing_binascii_fixed_passes(self):
        fixed = apply_common_quickfixes(_BROKEN_DES_ECB_PY, "python")
        self.assertIn("import binascii", fixed)
        self._assert_encrypt_ok(fixed, "DES", "ECB")

    def test_broken_ofb_fails_without_fix(self):
        ok, _, _ = self.tester.test(
            _BROKEN_DES_OFB_PY,
            "python",
            **_vec("DES", "OFB"),
            algorithm="DES",
            mode="OFB",
        )
        self.assertFalse(ok)

    def test_broken_cfb_fails_without_fix(self):
        ok, _, _ = self.tester.test(
            _BROKEN_DES_CFB_PY,
            "python",
            **_vec("DES", "CFB"),
            algorithm="DES",
            mode="CFB",
        )
        self.assertFalse(ok)

    def test_broken_ecb_fails_without_fix(self):
        ok, _, _ = self.tester.test(
            _BROKEN_DES_ECB_PY,
            "python",
            **_vec("DES", "ECB"),
            algorithm="DES",
            mode="ECB",
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
