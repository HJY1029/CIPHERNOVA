#include <openssl/evp.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

int main() {
    const char *plaintext_hex = getenv("TEST_PLAINTEXT");
    const char *key_hex = getenv("TEST_KEY");
    const char *iv_hex = getenv("TEST_IV");

    if (!plaintext_hex || !key_hex) {
        fprintf(stderr, "缺少环境变量 TEST_PLAINTEXT 或 TEST_KEY\n");
        return 1;
    }

    size_t plaintext_len = strlen(plaintext_hex) / 2;
    unsigned char *plaintext = (unsigned char *)malloc(plaintext_len);
    if (!plaintext) {
        fprintf(stderr, "内存分配失败\n");
        return 1;
    }
    for (size_t i = 0; i < plaintext_len; i++) {
        sscanf(&plaintext_hex[i * 2], "%2hhx", &plaintext[i]);
    }

    size_t key_len = strlen(key_hex) / 2;
    unsigned char *key = (unsigned char *)malloc(key_len);
    if (!key) {
        fprintf(stderr, "内存分配失败\n");
        free(plaintext);
        return 1;
    }
    for (size_t i = 0; i < key_len; i++) {
        sscanf(&key_hex[i * 2], "%2hhx", &key[i]);
    }

    unsigned char *iv = NULL;
    if (iv_hex) {
        size_t iv_len = strlen(iv_hex) / 2;
        iv = (unsigned char *)malloc(iv_len);
        if (!iv) {
            fprintf(stderr, "内存分配失败\n");
            free(plaintext);
            free(key);
            return 1;
        }
        for (size_t i = 0; i < iv_len; i++) {
            sscanf(&iv_hex[i * 2], "%2hhx", &iv[i]);
        }
    } else {
        iv = (unsigned char *)calloc(16, sizeof(unsigned char));
        if (!iv) {
            fprintf(stderr, "内存分配失败\n");
            free(plaintext);
            free(key);
            return 1;
        }
    }

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "创建加密上下文失败\n");
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }

    if (EVP_EncryptInit_ex(ctx, EVP_sm4_cfb128(), NULL, key, iv) != 1) {
        fprintf(stderr, "初始化加密失败\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }

    int len;
    int ciphertext_len = 0;
    unsigned char *ciphertext = malloc(plaintext_len + 32);
    if (!ciphertext) {
        fprintf(stderr, "内存分配失败\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }

    if (EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, plaintext_len) != 1) {
        fprintf(stderr, "加密更新失败\n");
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }
    ciphertext_len = len;

    if (EVP_EncryptFinal_ex(ctx, ciphertext + len, &len) != 1) {
        fprintf(stderr, "加密最终失败\n");
        free(ciphertext);
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }
    ciphertext_len += len;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    free(ciphertext);
    EVP_CIPHER_CTX_free(ctx);
    free(plaintext);
    free(key);
    if (iv != NULL) {
        free(iv);
    }

    return 0;
}