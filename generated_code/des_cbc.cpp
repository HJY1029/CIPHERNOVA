#include <iostream>
#include <string>
#include <openssl/evp.h>
#include <openssl/provider.h>

static void strip(std::string& s) {
    s.erase(0, s.find_first_not_of(" \t\n\r\f\v"));
    s.erase(s.find_last_not_of(" \t\n\r\f\v") + 1);
}

static int h2b(const std::string& h, unsigned char* b, int n) {
    for (int i = 0; i < n; ++i) {
        unsigned v;
        if (sscanf(h.substr(i * 2, 2).c_str(), "%2x", &v) != 1)
            return -1;
        b[i] = static_cast<unsigned char>(v);
    }
    return 0;
}

int main() {
    OSSL_PROVIDER_load(nullptr, "legacy");
    std::string ph = std::getenv("TEST_PLAINTEXT") ? std::getenv("TEST_PLAINTEXT") : "";
    std::string kh = std::getenv("TEST_KEY") ? std::getenv("TEST_KEY") : "";
    std::string ih = std::getenv("TEST_IV") ? std::getenv("TEST_IV") : "";

    if (ph.empty() || kh.empty() || ih.empty())
        return 1;

    strip(ph);
    strip(kh);
    strip(ih);

    int pl = ph.length() / 2;
    unsigned char pt[64], key[8], iv[8];
    if (ph.length() != 32 || kh.length() != 16 || ih.length() != 16 ||
        h2b(ph, pt, pl) != 0 || h2b(kh, key, 8) != 0 || h2b(ih, iv, 8) != 0)
        return 1;

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx)
        return 1;

    if (EVP_EncryptInit_ex(ctx, EVP_des_cbc(), nullptr, key, iv) != 1 ||
        EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    int o1 = 0, o2 = 0;
    if (EVP_EncryptUpdate(ctx, pt, &o1, pt, pl) != 1 ||
        EVP_EncryptFinal_ex(ctx, pt + o1, &o2) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    std::cout << "密文: ";
    for (int i = 0; i < o1 + o2; ++i)
        printf("%02x", pt[i]);
    std::cout << std::endl;

    EVP_CIPHER_CTX_free(ctx);
    return 0;
}