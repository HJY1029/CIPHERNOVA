#include <openssl/evp.h>
#include <openssl/provider.h>

#include <cstring>

static int h2b(const std::string &h, unsigned char *b, int n) {
    for (int i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(h.substr(2 * i, 2).c_str(), "%2x", &v) != 1)
            return -1;
        b[i] = static_cast<unsigned char>(v);
    }
    return 0;
}

int main() {
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
    (void)OSSL_PROVIDER_load(NULL, "legacy");
#endif

    unsigned char pt[16], key[8], iv[8], out[32];
    std::string ph = "your_hex_string_here";
    std::string kh = "your_hex_key_here";
    std::string ih = "your_hex_iv_here";

    if (ph.length() != 32 || kh.length() != 16 || ih.length() != 16)
        return 1;

    if (h2b(ph, pt, 16) || h2b(kh, key, 8) || h2b(ih, iv, 8))
        return 1;

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    EVP_EncryptInit_ex(ctx, EVP_des_ofb(), nullptr, key, iv);
    EVP_CIPHER_CTX_set_padding(ctx, 0);

    int o1 = 0, o2 = 0;
    EVP_EncryptUpdate(ctx, out, &o1, pt, 16);
    EVP_EncryptFinal_ex(ctx, out + o1, &o2);

    printf("密文: ");
    for (int i = 0; i < o1 + o2; i++)
        printf("%02x", out[i]);
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    return 0;
}