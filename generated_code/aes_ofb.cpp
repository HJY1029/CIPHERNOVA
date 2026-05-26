#include <openssl/evp.h>
#include <cstring>
#include <cstdio>

int main() {
    const char* test_plaintext_hex = std::getenv("TEST_PLAINTEXT");
    const char* test_key_hex = std::getenv("TEST_KEY");
    const char* test_iv_hex = std::getenv("TEST_IV");

    if (!test_plaintext_hex || !test_key_hex || !test_iv_hex) {
        return 1;
    }

    unsigned char plaintext[32];
    unsigned char key[16];
    unsigned char iv[16];

    size_t plaintext_len = std::strlen(test_plaintext_hex);
    size_t key_len = std::strlen(test_key_hex);
    size_t iv_len = std::strlen(test_iv_hex);

    if (plaintext_len != 32 * 2 || key_len != 16 * 2 || iv_len != 16 * 2) {
        return 1;
    }

    for (size_t i = 0; i < plaintext_len / 2; ++i) {
        sscanf(test_plaintext_hex + 2 * i, "%2hhx", &plaintext[i]);
    }
    for (size_t i = 0; i < key_len / 2; ++i) {
        sscanf(test_key_hex + 2 * i, "%2hhx", &key[i]);
    }
    for (size_t i = 0; i < iv_len / 2; ++i) {
        sscanf(test_iv_hex + 2 * i, "%2hhx", &iv[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx || EVP_EncryptInit_ex(ctx, EVP_aes_128_ofb(), NULL, key, iv) != 1 ||
        EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    unsigned char ciphertext[32];
    int ciphertext_len = 0;

    if (EVP_EncryptUpdate(ctx, ciphertext, &ciphertext_len, plaintext, 32) != 1 ||
        EVP_EncryptFinal_ex(ctx, ciphertext + ciphertext_len, &ciphertext_len) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    EVP_CIPHER_CTX_free(ctx);

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; ++i) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    return 0;
}