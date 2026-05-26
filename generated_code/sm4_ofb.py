import os
from gmssl.sm4 import CryptSM4, SM4_ENCRYPT, bytes_to_list, list_to_bytes

def sm4_ofb_encrypt(key, iv, pt):
    c = CryptSM4()
    c.set_key(key, SM4_ENCRYPT)
    R = iv
    out = bytearray()
    for off in range(0, len(pt), 16):
        block = pt[off:off + 16]
        o = list_to_bytes(c.one_round(c.sk, bytes_to_list(R)))
        c_block = bytes(block[i] ^ o[i] for i in range(len(block)))
        out.extend(c_block)
        R = o
    return bytes(out)

k = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
iv = bytes.fromhex(os.getenv("TEST_IV", "").strip())
pt = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())
print("密文:", sm4_ofb_encrypt(k, iv, pt).hex().lower())