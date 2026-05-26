#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <openssl/evp.h>

unsigned char* h2b(const char* hex, size_t len) {
    unsigned char* bytes = (unsigned char*)malloc(len / 2);
    for (size_t i = 0; i < len; i += 2) {
        sscanf(hex + i, "%2hhx", &bytes[i / 2]);
    }
    return bytes;
}

int main() {
    const char* kh = "f7264413a84c0e7cd5369b6d4b4f029c";
    const char* ih = "0000000000000000";
    const char* ph = "0000000000000000";
    const char* ah = "0000000000000000";
    const char* pt = "Hello, World!";

    unsigned char key[32] = {0};
    unsigned char iv[12] = {0};
    unsigned char aad[16] = {0};
    unsigned char tag[16] = {0};

    memcpy(key, h2b(kh, 64), 32);
    memcpy(iv, h2b(ih, 24), 12);
    memcpy(aad, h2b(ah, 32), 16);

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "Failed to create cipher context\n");
        return 1;
    }

    int o1, o2, l;
    unsigned char* ciphertext = (unsigned char*)malloc(strlen(pt) + 16);
    if (!ciphertext) {
        EVP_CIPHER_CTX_free(ctx);
        fprintf(stderr, "Failed to allocate memory for ciphertext\n");
        return 1;
    }

    if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, key, iv)) {
        ERR_print_errors_fp(stderr);
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &o1, (unsigned char*)aad, 16)) {
        ERR_print_errors_fp(stderr);
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    if (1 != EVP_EncryptUpdate(ctx, ciphertext + o1, &o2, (unsigned char*)pt, strlen(pt))) {
        ERR_print_errors_fp(stderr);
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    l = o1 + o2;

    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + l, &o2)) {
        ERR_print_errors_fp(stderr);
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    l += o2;

    if (1 != EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag)) {
        ERR_print_errors_fp(stderr);
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    printf("密文: ");
    for (size_t i = 0; i < l; ++i) {
        printf("%02x", ciphertext[i]);
    }
    for (size_t i = 0; i < 16; ++i) {
        printf("%02x", tag[i]);
    }
    printf("\n");

    free(ciphertext);
    EVP_CIPHER_CTX_free(ctx);
    return 0;
}