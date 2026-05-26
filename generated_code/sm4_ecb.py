from gmssl import sm4, func

def encrypt_ecb(plaintext, key):
    crypt_sm4 = sm4.CryptSM4()
    crypt_sm4.set_key(key, sm4.SM4_ENCRYPT)
    ciphertext = crypt_sm4.crypt_ecb(plaintext)
    return ciphertext

if __name__ == "__main__":
    import os
    plaintext_hex = os.getenv("TEST_PLAINTEXT", "").strip()
    key_hex = os.getenv("TEST_KEY", "").strip()

    if not plaintext_hex or not key_hex:
        print("密钥或明文缺失")
        sys.exit(1)

    plaintext = bytes.fromhex(plaintext_hex)
    key = bytes.fromhex(key_hex)

    ciphertext = encrypt_ecb(plaintext, key)
    print(f"密文: {ciphertext.hex().lower()}")