#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <openssl/evp.h>

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
    char *ph = getenv("TEST_PLAINTEXT"), *kh = getenv("TEST_KEY"), *ih = getenv("TEST_IV");
    if (!ph || !kh || !ih)
        return 1;

    strip(ph); strip(kh); strip(ih);

    if (strlen(ph) != 32 || strlen(kh) != 32 || strlen(ih) != 32)
        return 1;

    unsigned char pt[16], key[16], iv[16];
    if (h2b(ph, pt, 16) || h2b(kh, key, 16) || h2b(ih, iv, 16))
        return 1;

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx)
        return 1;

    if (!EVP_EncryptInit_ex(ctx, EVP_sm4_cbc(), NULL, key, iv))
        return 1;

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    int o1 = 0, o2 = 0;
    unsigned char out[16];
    unsigned char *ciphertext = out;
    if (!EVP_EncryptUpdate(ctx, out, &o1, pt, 16))
        return 1;

    if (!EVP_EncryptFinal_ex(ctx, out + o1, &o2))
        return 1;

    printf("密文: ");
    for (int i = 0; i < o1 + o2; i++)
        printf("%02x", out[i]);
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    return 0;
}