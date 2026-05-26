#include <iostream>
#include <sstream>
#include <iomanip>
#include <openssl/evp.h>
#include <openssl/provider.h>

static void strip(std::string &s) {
    s.erase(remove_if(s.begin(), s.end(), isspace), s.end());
}

static int hex_to_bytes(const std::string &hex, unsigned char *bytes, int n) {
    for (int i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(hex.substr(i * 2, 2).c_str(), "%2x", &v) != 1)
            return -1;
        bytes[i] = static_cast<unsigned char>(v);
    }
    return 0;
}

int main() {
    OSSL_PROVIDER *legacy = OSSL_PROVIDER_load(nullptr, "legacy");
    if (!legacy) {
        std::cerr << "无法加载legacy provider" << std::endl;
        return 1;
    }

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        std::cerr << "无法创建加密上下文" << std::endl;
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    const unsigned char key[] = "0123456789abcdef";
    if (1 != EVP_EncryptInit_ex(ctx, EVP_des_ecb(), NULL, key, NULL)) {
        std::cerr << "初始化加密失败" << std::endl;
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    const unsigned char plaintext[] = "0123456789abcdef";
    int ciphertext_len = sizeof(plaintext);
    unsigned char ciphertext[ciphertext_len];

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &ciphertext_len, plaintext, sizeof(plaintext))) {
        std::cerr << "更新加密失败" << std::endl;
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    int o2;
    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + ciphertext_len, &o2)) {
        std::cerr << "最终加密失败" << std::endl;
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    ciphertext_len += o2;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    OSSL_PROVIDER_unload(legacy);
    return 0;
}