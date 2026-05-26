from Crypto.Cipher import AES
import os

key = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
nonce = bytes.fromhex(os.getenv("TEST_IV", "99AA3E68ED8173A0EED06684").strip())
aad = bytes.fromhex(os.getenv("TEST_AAD", "4D23C3CEC334B49BDB370C437FEC78DE").strip())
pt = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())

c = AES.new(key, AES.MODE_GCM, nonce=nonce)
c.update(aad)
ct, tag = c.encrypt_and_digest(pt)

print("密文:", (ct + tag).hex().lower())