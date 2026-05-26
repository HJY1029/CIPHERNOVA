#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <openssl/evp.h>
#include <openssl/provider.h>

static void strip(char *s) {
    char *d = s;
    while (*s) {
        if (!isspace((unsigned char)*s))
            *d++ = *s;
        s++;
    }
    *d = 0;
}

static int h2b(const char *h, unsigned char *b, int n) {
    for (int i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(h + 2 * i, "%2x", &v) != 1)
            return -1;
        b[i] = (unsigned char)v;
    }
    return 0;
}

int main(void) {
    OSSL_PROVIDER *legacy = OSSL_PROVIDER_load(NULL, "legacy");
    if (!legacy) {
        fprintf(stderr, "无法加载legacy provider\n");
        return 1;
    }

    char *ph = getenv("TEST_PLAINTEXT"), *kh = getenv("TEST_KEY"), *ih = getenv("TEST_IV");
    if (!ph || !kh || !ih)
        return 1;

    strip(ph);
    strip(kh);
    strip(ih);

    unsigned char pt[16], key[8], iv[8], out[32];
    if (strlen(ph) != 32 || strlen(kh) != 16 || strlen(ih) != 16)
        return 1;

    if (h2b(ph, pt, 16) || h2b(kh, key, 8) || h2b(ih, iv, 8))
        return 1;

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!EVP_EncryptInit_ex(ctx, EVP_des_cfb8(), NULL, key, iv)) {
        fprintf(stderr, "初始化加密失败\n");
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    int o1 = 0, o2 = 0;
    if (!EVP_EncryptUpdate(ctx, out, &o1, pt, 16)) {
        fprintf(stderr, "加密更新失败\n");
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    if (!EVP_EncryptFinal_ex(ctx, out + o1, &o2)) {
        fprintf(stderr, "加密最终失败\n");
        EVP_CIPHER_CTX_free(ctx);
        OSSL_PROVIDER_unload(legacy);
        return 1;
    }

    int ciphertext_len = o1 + o2;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; i++) {
        printf("%02x", out[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    OSSL_PROVIDER_unload(legacy);

    return 0;
}