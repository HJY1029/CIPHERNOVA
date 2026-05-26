import os
from Crypto.Cipher import DES

def encrypt_des_cbc():
    plaintext = bytes.fromhex(os.getenv("TEST_PLAINTEXT").strip())
    key = bytes.fromhex(os.getenv("TEST_KEY").strip())
    iv = bytes.fromhex(os.getenv("TEST_IV").strip())

    if len(key) != 8 or len(iv) != 8:
        return 1

    cipher = DES.new(key, DES.MODE_CBC, iv)
    ct = cipher.encrypt(plaintext)

    print(f"密文: {ct.hex().lower()}")
    return 0

if __name__ == "__main__":
    exit(encrypt_des_cbc())