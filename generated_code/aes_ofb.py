import os
from Crypto.Cipher import AES

key = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
iv = bytes.fromhex(os.getenv("TEST_IV", "").strip())
plaintext = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())

cipher = AES.new(key, AES.MODE_OFB, iv)
ciphertext = cipher.encrypt(plaintext)

print(f"密文: {ciphertext.hex().lower()}")