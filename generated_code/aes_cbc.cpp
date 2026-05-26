#include <openssl/evp.h>
#include <cstring>

int main() {
    const char* test_plaintext_hex = std::getenv("TEST_PLAINTEXT");
    const char* test_key_hex = std::getenv("TEST_KEY");
    const char* test_iv_hex = std::getenv("TEST_IV");

    if (!test_plaintext_hex || !test_key_hex || !test_iv_hex) {
        return 1;
    }

    // Convert hex strings to binary
    size_t plaintext_len = strlen(test_plaintext_hex) / 2;
    unsigned char* plaintext = new unsigned char[plaintext_len];
    for (size_t i = 0; i < plaintext_len; ++i) {
        sscanf(&test_plaintext_hex[i * 2], "%2hhx", &plaintext[i]);
    }

    size_t key_len = strlen(test_key_hex) / 2;
    unsigned char* key = new unsigned char[key_len];
    for (size_t i = 0; i < key_len; ++i) {
        sscanf(&test_key_hex[i * 2], "%2hhx", &key[i]);
    }

    size_t iv_len = strlen(test_iv_hex) / 2;
    unsigned char* iv = new unsigned char[iv_len];
    for (size_t i = 0; i < iv_len; ++i) {
        sscanf(&test_iv_hex[i * 2], "%2hhx", &iv[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        return 1;
    }

    if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_128_cbc(), NULL, key, iv)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    unsigned char* ciphertext = new unsigned char[plaintext_len + EVP_CIPHER_block_size(EVP_aes_128_cbc())];
    int update_len, final_len;

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &update_len, plaintext, plaintext_len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        delete[] ciphertext;
        return 1;
    }

    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + update_len, &final_len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] key;
        delete[] iv;
        delete[] ciphertext;
        return 1;
    }

    int len = update_len + final_len;

    printf("密文: ");
    for (int i = 0; i < len; i++) {
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