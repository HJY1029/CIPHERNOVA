#include <openssl/evp.h>
#include <cstring>
#include <cstdio>

void strip(std::string& s) {
    s.erase(remove_if(s.begin(), s.end(), isspace), s.end());
}

std::vector<unsigned char> hex_to_bytes(const std::string& hex) {
    if (hex.length() % 2 != 0) return {};
    std::vector<unsigned char> bytes(hex.length() / 2);
    for (size_t i = 0; i < hex.length(); i += 2) {
        sscanf(hex.substr(i, 2).c_str(), "%2hhx", &bytes[i / 2]);
    }
    return bytes;
}

int main() {
    OSSL_PROVIDER *legacy = OSSL_PROVIDER_load(nullptr, "legacy");
    if (!legacy) {
        fprintf(stderr, "无法加载legacy provider\n");
        return 1;
    }

    std::string key_hex = "0123456789abcdef";
    std::string iv_hex = "1234567890abcdef";
    std::string plaintext_hex = "0011223344556677";

    std::vector<unsigned char> key = hex_to_bytes(key_hex);
    std::vector<unsigned char> iv = hex_to_bytes(iv_hex);
    std::vector<unsigned char> plaintext = hex_to_bytes(plaintext_hex);

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "无法创建加密上下文\n");
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    if (EVP_EncryptInit_ex(ctx, EVP_des_cfb8(), nullptr, key.data(), iv.data()) != 1) {
        fprintf(stderr, "初始化加密失败\n");
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    unsigned char ciphertext[128];
    int len = 0;

    if (EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext.data(), plaintext.size()) != 1) {
        fprintf(stderr, "加密更新失败\n");
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    int ciphertext_len = len;

    if (EVP_EncryptFinal_ex(ctx, ciphertext + len, &len) != 1) {
        fprintf(stderr, "加密最终失败\n");
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    ciphertext_len += len;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; ++i) {
        printf("%02x", static_cast<int>(ciphertext[i]));
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    OSSL_PROVIDER_unload(legacy);
    return 0;
}