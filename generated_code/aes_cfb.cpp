#include <openssl/evp.h>
#include <stdio.h>

int main() {
    int len = 0, ciphertext_len = 0;

    const char* test_plaintext = std::getenv("TEST_PLAINTEXT");
    const char* test_key = std::getenv("TEST_KEY");
    const char* test_iv = std::getenv("TEST_IV");

    if (!test_plaintext || !test_key || !test_iv) {
        return 1;
    }

    unsigned char plaintext[16];
    unsigned char key[16];
    unsigned char iv[16];

    for (int i = 0; i < 16; ++i) {
        sscanf(test_plaintext + 2 * i, "%2hhx", &plaintext[i]);
        sscanf(test_key + 2 * i, "%2hhx", &key[i]);
        sscanf(test_iv + 2 * i, "%2hhx", &iv[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;

    if (EVP_EncryptInit_ex(ctx, EVP_aes_128_cfb8(), NULL, key, iv) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    unsigned char ciphertext[32];
    int len, ciphertext_len = 0;

    if (EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, 16) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len += len;

    if (EVP_EncryptFinal_ex(ctx, ciphertext + len, &len) != 1) {
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