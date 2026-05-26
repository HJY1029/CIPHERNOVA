#include <openssl/evp.h>
#include <cstring>
#include <cstdio>

int main() {
    const char* plaintext_hex = std::getenv("TEST_PLAINTEXT");
    const char* key_hex = std::getenv("TEST_KEY");

    if (!plaintext_hex || !key_hex) {
        return 1;
    }

    size_t plaintext_len = strlen(plaintext_hex) / 2;
    size_t key_len = strlen(key_hex) / 2;

    if (plaintext_len != 32 || key_len != 32) {
        return 1;
    }

    unsigned char* plaintext = new unsigned char[plaintext_len];
    unsigned char* key = new unsigned char[key_len];

    for (size_t i = 0; i < plaintext_len; ++i) {
        sscanf(plaintext_hex + i * 2, "%2hhx", &plaintext[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        delete[] plaintext;
        delete[] key;
        return 1;
    }

    if (1 != EVP_EncryptInit_ex(ctx, EVP_sm4_ecb(), NULL, key, NULL)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    unsigned char* ciphertext = new unsigned char[plaintext_len];
    int len;

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, plaintext_len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] ciphertext;
        return 1;
    }

    int ciphertext_len = len;

    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + len, &len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] ciphertext;
        return 1;
    }
    ciphertext_len += len;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; ++i) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    delete[] plaintext;
    delete[] key;
    delete[] ciphertext;

    return 0;
}