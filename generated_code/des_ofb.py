import os
from Crypto.Cipher import DES

def encrypt_des_ofb(plaintext, key, iv):
    cipher = DES.new(bytes.fromhex(key), DES.MODE_OFB, bytes.fromhex(iv))
    ciphertext = cipher.encrypt(plaintext.encode('utf-8'))
    return ciphertext.hex().lower()

if __name__ == "__main__":
    test_plaintext = os.getenv("TEST_PLAINTEXT", "").strip()
    test_key = os.getenv("TEST_KEY", "").strip()
    test_iv = os.getenv("TEST_IV", "0" * 16).strip()

    if not all([test_plaintext, test_key, test_iv]):
        print(1)
        exit(1)

    ct = encrypt_des_ofb(test_plaintext, test_key, test_iv)
    print(f"密文: {ct}")