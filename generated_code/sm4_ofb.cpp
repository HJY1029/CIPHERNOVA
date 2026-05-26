#include <openssl/evp.h>
#include <cstdlib>
#include <iostream>
#include <string>
#include <iomanip>

int main() {
    std::string ph = std::getenv("TEST_PLAINTEXT");
    std::string kh = std::getenv("TEST_KEY");
    std::string ih = std::getenv("TEST_IV");

    if (ph.empty() || kh.empty() || ih.empty()) return 1;

    unsigned char pt[32], key[32], iv[32];
    size_t plaintext_len = ph.size() / 2;
    size_t key_len = kh.size() / 2;
    size_t iv_len = ih.size() / 2;

    if (plaintext_len != 16 || key_len != 16 || iv_len != 16) return 1;

    for (size_t i = 0; i < plaintext_len; ++i) {
        sscanf(ph.substr(i * 2, 2).c_str(), "%2hhx", &pt[i]);
    }
    for (size_t i = 0; i < key_len; ++i) {
        sscanf(kh.substr(i * 2, 2).c_str(), "%2hhx", &key[i]);
    }
    for (size_t i = 0; i < iv_len; ++i) {
        sscanf(ih.substr(i * 2, 2).c_str(), "%2hhx", &iv[i]);
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;

    if (!EVP_EncryptInit_ex(ctx, EVP_sm4_ofb(), NULL, key, iv)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    unsigned char ciphertext[32];
    int len = 0;
    int ciphertext_len = 0;

    if (!EVP_EncryptUpdate(ctx, ciphertext, &len, pt, plaintext_len)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len += len;

    if (!EVP_EncryptFinal_ex(ctx, ciphertext + len, &len)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len += len;

    std::cout << "密文: ";
    for (size_t i = 0; i < ciphertext_len; ++i) {
        std::cout << std::hex << std::setfill('0') << std::setw(2) << static_cast<int>(ciphertext[i]);
    }
    std::cout << std::endl;

    EVP_CIPHER_CTX_free(ctx);
    return 0;
}