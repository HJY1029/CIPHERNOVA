#include <openssl/evp.h>
#include <cstring>
#include <cstdio>

int main() {
    const char* plaintext_hex = std::getenv("TEST_PLAINTEXT");
    const char* key_hex = std::getenv("TEST_KEY");
    const char* iv_hex = std::getenv("TEST_IV");

    if (!plaintext_hex || !key_hex) return 1;

    unsigned char plaintext[32];
    size_t plaintext_len = strlen(plaintext_hex) / 2;
    for (size_t i = 0; i < plaintext_len; ++i) {
        sscanf(&plaintext_hex[i * 2], "%2hhx", &plaintext[i]);
    }

    unsigned char key[16];
    size_t key_len = strlen(key_hex) / 2;
    for (size_t i = 0; i < key_len; ++i) {
        sscanf(&key_hex[i * 2], "%2hhx", &key[i]);
    }

    unsigned char iv[16];
    size_t iv_len = strlen(iv_hex) / 2;
    for (size_t i = 0; i < iv_len; ++i) {
        sscanf(&iv_hex[i * 2], "%2hhx", &iv[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;

    if (1 != EVP_EncryptInit_ex(ctx, EVP_sm4_cfb128(), NULL, key, iv)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    unsigned char ciphertext[32];
    int len;
    int ciphertext_len = 0;

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, plaintext_len)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len = len;

    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + len, &len)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len += len;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; ++i) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    return 0;
}