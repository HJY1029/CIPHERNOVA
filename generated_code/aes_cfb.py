import os
from Crypto.Cipher import AES

# 从环境变量读取密钥和IV，如果不存在则从stdin读取
k = bytes.fromhex(os.getenv("TEST_KEY", "").strip())
iv = bytes.fromhex(os.getenv("TEST_IV", "").strip())

# 如果环境变量中没有提供密钥或IV，则从stdin读取
if not k or not iv:
    print("请输入密钥和IV（十六进制格式）：")
    k = bytes.fromhex(input("密钥: ").strip())
    iv = bytes.fromhex(input("IV: ").strip())

# 检查密钥和IV的长度是否正确
if len(k) != 16 or len(iv) != 16:
    print("密钥必须为16字节，IV必须为16字节")
    exit(1)

# 创建AES CFB-8模式的加密对象
cipher = AES.new(k, AES.MODE_CFB, iv, segment_size=8)

# 从环境变量或stdin读取明文
pt = bytes.fromhex(os.getenv("TEST_PLAINTEXT", "").strip())

# 如果环境变量中没有提供明文，则从stdin读取
if not pt:
    print("请输入明文（十六进制格式）：")
    pt = bytes.fromhex(input("明文: ").strip())

# 加密明文并输出密文
ct = cipher.encrypt(pt)
print(f"密文: {ct.hex().lower()}")
exit(0)