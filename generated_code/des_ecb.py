import os
from Crypto.Cipher import DES

def encrypt_des_ecb(plaintext, key):
    # 确保密钥和明文长度正确
    if len(key) != 16 or len(plaintext) % 8 != 0:
        raise ValueError("密钥必须是16字节的十六进制字符串，明文长度必须是8的倍数")
    
    # 创建DES ECB模式加密对象
    cipher = DES.new(bytes.fromhex(key), DES.MODE_ECB)
    
    # 加密并返回十六进制格式的密文
    ct_bytes = cipher.encrypt(plaintext.encode('utf-8'))
    return ct_bytes.hex().lower()

if __name__ == "__main__":
    # 从环境变量读取输入，如果不存在则从stdin读取
    plaintext = os.getenv("TEST_PLAINTEXT", input()).strip()
    key = os.getenv("TEST_KEY", input()).strip()
    
    try:
        ct_hex = encrypt_des_ecb(plaintext, key)
        print(f"密文: {ct_hex}")
    except ValueError as e:
        print(e)
        exit(1)