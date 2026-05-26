import os
from gmssl.sm4 import CryptSM4, SM4_ENCRYPT

k = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
iv = bytes.fromhex(os.getenv("TEST_IV", "").strip() or "0" * 32)
pt = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())

s = CryptSM4()
s.set_key(k, SM4_ENCRYPT)

ct = s.crypt_cbc(iv, pt)
print(f"密文: {ct.hex().lower()}")