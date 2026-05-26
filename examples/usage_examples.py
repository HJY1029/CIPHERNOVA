"""
使用示例 - 展示多语言代码生成和验证功能
"""
import asyncio
from agent.crypto_agent import CryptoAgent

async def example_des_cbc_python():
    """生成DES-CBC Python代码示例（带验证）"""
    agent = CryptoAgent(enable_validation=True)
    filepath, validation_result = await agent.generate_and_save(
        algorithm="DES",
        mode="CBC",
        operation="加密解密",
        language="python",
        validate=True
    )
    print(f"代码已保存到: {filepath}")
    if validation_result:
        success, output = validation_result
        if success:
            print("✓ 代码验证通过！")
        else:
            print(f"⚠ 代码验证失败: {output}")

async def example_aes_gcm_c():
    """生成AES-GCM C代码示例"""
    agent = CryptoAgent(enable_validation=True)
    filepath, validation_result = await agent.generate_and_save(
        algorithm="AES",
        mode="GCM",
        operation="加密解密",
        language="c",
        validate=True
    )
    print(f"代码已保存到: {filepath}")
    if validation_result:
        success, output = validation_result
        if success:
            print("✓ 代码验证通过！")
        else:
            print(f"⚠ 代码验证失败: {output}")

async def example_rsa_cpp():
    """生成RSA C++代码示例"""
    agent = CryptoAgent(enable_validation=True)
    filepath, validation_result = await agent.generate_and_save(
        algorithm="RSA",
        operation="完整的密钥生成、加密、解密、签名和验证",
        language="cpp",
        validate=True
    )
    print(f"代码已保存到: {filepath}")
    if validation_result:
        success, output = validation_result
        if success:
            print("✓ 代码验证通过！")
        else:
            print(f"⚠ 代码验证失败: {output}")

async def example_sm4_python():
    """生成SM4 Python代码示例（无验证）"""
    agent = CryptoAgent(enable_validation=False)
    code = await agent.generate_code(
        algorithm="SM4",
        mode="CBC",
        operation="加密解密",
        language="python",
        额外要求="使用gmssl库实现"
    )
    print(code)

async def example_list_supported():
    """列出支持的算法和语言"""
    agent = CryptoAgent()
    
    print("支持的算法:")
    algorithms = agent.list_supported_algorithms()
    for alg, modes in algorithms.items():
        print(f"  {alg}: {', '.join(modes) if modes else 'N/A'}")
    
    print("\n支持的编程语言:")
    languages = agent.list_supported_languages()
    for lang in languages:
        print(f"  - {lang}")

if __name__ == "__main__":
    # 运行示例
    print("=== 示例1: DES-CBC Python代码（带验证）===")
    asyncio.run(example_des_cbc_python())
    
    print("\n=== 示例2: 列出支持的算法和语言 ===")
    asyncio.run(example_list_supported())
