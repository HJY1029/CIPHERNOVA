#include <openssl/evp.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#define AES_BLOCK_BYTES 16

void remove_whitespace(char *str) {
    char *src = str, *dst = str;
    while (*src) {
        if (!isspace((unsigned char)*src)) {
            *dst++ = *src;
        }
        src++;
    }
    *dst = '\0';
}

int main() {
    const char *plaintext_hex = getenv("TEST_PLAINTEXT");
    const char *key_hex = getenv("TEST_KEY");

    if (!plaintext_hex || !key_hex) {
        fprintf(stderr, "Environment variables TEST_PLAINTEXT and TEST_KEY must be set.\n");
        return 1;
    }

    remove_whitespace((char *)plaintext_hex);
    remove_whitespace((char *)key_hex);

    size_t plaintext_len = strlen(plaintext_hex) / 2;
    size_t key_len = strlen(key_hex) / 2;

    unsigned char *plaintext = (unsigned char *)malloc(plaintext_len);
    unsigned char *key = (unsigned char *)malloc(key_len);

    for (size_t i = 0; i < plaintext_len; i++) {
        sscanf(&plaintext_hex[i * 2], "%2hhx", &plaintext[i]);
    }
    for (size_t i = 0; i < key_len; i++) {
        sscanf(&key_hex[i * 2], "%2hhx", &key[i]);
    }

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "Failed to create EVP_CIPHER_CTX.\n");
        free(plaintext);
        free(key);
        return 1;
    }

    int len;
    int ciphertext_len = 0;
    unsigned char *ciphertext = malloc(plaintext_len + AES_BLOCK_BYTES);  // 预留填充空间

    if (key_len == 16) {
        EVP_EncryptInit_ex(ctx, EVP_aes_128_ecb(), NULL, key, NULL);
    } else if (key_len == 24) {
        EVP_EncryptInit_ex(ctx, EVP_aes_192_ecb(), NULL, key, NULL);
    } else if (key_len == 32) {
        EVP_EncryptInit_ex(ctx, EVP_aes_256_ecb(), NULL, key, NULL);
    } else {
        fprintf(stderr, "Unsupported key length.\n");
        free(plaintext);
        free(key);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    if (!EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, plaintext_len)) {
        fprintf(stderr, "Encryption failed.\n");
        free(plaintext);
        free(key);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len = len;

    if (!EVP_EncryptFinal_ex(ctx, ciphertext + ciphertext_len, &len)) {
        fprintf(stderr, "Encryption failed.\n");
        free(plaintext);
        free(key);
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len += len;

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    free(plaintext);
    free(key);
    EVP_CIPHER_CTX_free(ctx);

    return 0;
}