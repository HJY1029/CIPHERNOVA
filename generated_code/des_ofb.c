#include <openssl/evp.h>
#include <openssl/provider.h>

#include <string.h>

// DES-OFB 加密函数
static void des_ofb_encrypt(const unsigned char *key, const unsigned char *plaintext, unsigned char *ciphertext, size_t length) {
    unsigned char iv[8] = {0}; // 初始化向量
    unsigned char keystream[8];

    for (size_t i = 0; i < length; i += 8) {
        // 使用 DES 加密 IV 得到密钥流
        EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
        EVP_EncryptInit_ex(ctx, EVP_des_ofb(), NULL, key, iv);
        EVP_EncryptUpdate(ctx, keystream, NULL, iv, sizeof(iv));
        EVP_EncryptFinal_ex(ctx, keystream + 8, NULL);
        EVP_CIPHER_CTX_free(ctx);

        // 使用密钥流加密明文
        for (size_t j = 0; j < 8 && i + j < length; j++) {
            ciphertext[i + j] = plaintext[i + j] ^ keystream[j];
        }

        // 更新 IV 为密钥流的前 8 字节
        memcpy(iv, keystream, sizeof(iv));
    }
}

int main() {
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
    (void)OSSL_PROVIDER_load(NULL, "legacy");
#endif

    unsigned char key[8] = {0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0};
    unsigned char plaintext[] = "Hello, World!\0";
    unsigned char ciphertext[sizeof(plaintext)];

    des_ofb_encrypt(key, plaintext, ciphertext, sizeof(plaintext));

    printf("密文: ");
    for (size_t i = 0; i < sizeof(ciphertext); i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    return 0;
}