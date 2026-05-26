import os
from gmssl import sm4

def sm4_cfb_encrypt(plaintext, key, iv):
    crypt = sm4.CryptSM4()
    crypt.set_key(key, sm4.SM4_ENCRYPT)
    
    ciphertext = bytearray()
    R = bytes.fromhex(iv)
    
    for i in range(0, len(plaintext), 16):
        block = plaintext[i:i+16]
        o = bytes(crypt.one_round(crypt.sk, bytes_to_list(R)))
        c_block = bytes(b^o for b, o in zip(block, o))
        ciphertext.extend(c_block)
        R = c_block
    
    return ciphertext.hex()

def main():
    key_hex = os.getenv("TEST_KEY", "").strip()
    iv_hex = os.getenv("TEST_IV", "").strip()
    plaintext_hex = os.getenv("TEST_PLAINTEXT", "").strip()
    
    if not key_hex or len(key_hex) != 32:
        return 1
    if not iv_hex or len(iv_hex) != 32:
        iv_hex = "00" * 32
    
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    plaintext = bytes.fromhex(plaintext_hex)
    
    ciphertext = sm4_cfb_encrypt(plaintext, key, iv)
    print(f"密文: {ciphertext}")

if __name__ == "__main__":
    main()