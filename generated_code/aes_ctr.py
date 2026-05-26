import os
from Crypto.Cipher import AES

k = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
iv = bytes.fromhex(os.getenv("TEST_IV", "").strip() or "0" * 32)
pt = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())

cipher = AES.new(k, AES.MODE_CTR, counter=AES.Counter(iv))
ct = cipher.encrypt(pt)

print(f"密文: {ct.hex().lower()}")
exit(0)