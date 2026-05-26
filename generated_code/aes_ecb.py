import os
from Crypto.Cipher import AES

k = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
pt = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())

cipher = AES.new(k, AES.MODE_ECB)
ct = cipher.encrypt(pt)

print(f"密文: {ct.hex().lower()}")