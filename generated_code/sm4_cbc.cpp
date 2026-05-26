#include <openssl/evp.h>
#include <cstring>
#include <cstdio>

int main() {
    const char* plaintext_hex = std::getenv("TEST_PLAINTEXT");
    const char* key_hex = std::getenv("TEST_KEY");
    const char* iv_hex = std::getenv("TEST_IV");

    if (!plaintext_hex || !key_hex || !iv_hex) return 1;

    size_t plaintext_len = strlen(plaintext_hex) / 2;
    unsigned char* plaintext = new unsigned char[plaintext_len];
    for (size_t i = 0; i < plaintext_len; ++i) {
        sscanf(plaintext_hex + i * 2, "%2hhx", &plaintext[i]);
    }

    size_t key_len = strlen(key_hex) / 2;
    if (key_len != 16) return 1;

    unsigned char* key = new unsigned char[key_len];
    for (size_t i = 0; i < key_len; ++i) {
        sscanf(key_hex + i * 2, "%2hhx", &key[i]);
    }

    size_t iv_len = strlen(iv_hex) / 2;
    if (iv_len != 16) return 1;

    unsigned char* iv = new unsigned char[iv_len];
    for (size_t i = 0; i < iv_len; ++i) {
        sscanf(iv_hex + i * 2, "%2hhx", &iv[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;

    if (1 != EVP_EncryptInit_ex(ctx, EVP_sm4_cbc(), NULL, key, iv)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    int len = 0;
    if (1 != EVP_EncryptUpdate(ctx, NULL, &len, plaintext, plaintext_len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        return 1;
    }

    unsigned char* ciphertext = new unsigned char[plaintext_len + EVP_CIPHER_block_size(EVP_sm4_cbc())];
    if (!ciphertext) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        return 1;
    }

    int final_len = 0;
    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + len, &final_len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        delete[] ciphertext;
        return 1;
    }

    size_t ciphertext_len = len + final_len;

    printf("密文: ");
    for (size_t i = 0; i < ciphertext_len; ++i) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    delete[] plaintext;
    delete[] key;
    delete[] iv;
    delete[] ciphertext;

    return 0;
}