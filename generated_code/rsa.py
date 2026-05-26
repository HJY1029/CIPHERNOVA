import os
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes

def generate_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key

def encrypt(public_key, plaintext):
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return ciphertext

def sign(private_key, data):
    signature = private_key.sign(
        data,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return signature

def verify(public_key, data, signature):
    try:
        public_key.verify(
            signature,
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return True
    except Exception as e:
        print(e)
        return False

def main():
    plaintext = bytes.fromhex(os.getenv("TEST_PLAINTEXT", input("请输入明文（16进制）：").strip()))
    public_key_n = int(os.getenv("TEST_PUBLIC_KEY_N", input("请输入公钥 n（16进制）：").strip()), 16)
    public_key_e = int(os.getenv("TEST_PUBLIC_KEY_E", input("请输入公钥 e（16进制）：").strip()), 16)
    
    private_key_n = int(os.getenv("TEST_PRIVATE_KEY_N", input("请输入私钥 n（16进制）：").strip()), 16)
    private_key_d = int(os.getenv("TEST_PRIVATE_KEY_D", input("请输入私钥 d（16进制）：").strip()), 16)
    
    public_key = rsa.RSAPublicNumbers(public_exponent=public_key_e, modulus=public_key_n).public_key()
    private_key = rsa.RSAPrivateNumbers(private_exponent=private_key_d, public_numbers=public_key.public_numbers()).private_key()

    ciphertext = encrypt(public_key, plaintext)
    print("密文:", ciphertext.hex().lower())

if __name__ == "__main__":
    main()