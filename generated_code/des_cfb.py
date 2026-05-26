import os

def encrypt_des_cfb():
    plaintext = bytes.fromhex(os.getenv("TEST_PLAINTEXT").strip())
    key = bytes.fromhex(os.getenv("TEST_KEY").strip())
    iv = bytes.fromhex(os.getenv("TEST_IV", "0" * 16).strip())

    from Crypto.Cipher import DES
    cipher = DES.new(key, DES.MODE_CFB, iv, segment_size=8)
    ct = cipher.encrypt(plaintext)

    print(f"密文: {ct.hex().lower()}")

if __name__ == "__main__":
    encrypt_des_cfb()