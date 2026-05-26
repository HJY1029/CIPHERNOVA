#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <openssl/evp.h>

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
    const char *iv_hex = getenv("TEST_IV");

    if (!plaintext_hex || !key_hex) {
        fprintf(stderr, "Missing environment variables.\n");
        return 1;
    }

    remove_whitespace((char *)plaintext_hex);
    remove_whitespace((char *)key_hex);

    size_t plaintext_len = strlen(plaintext_hex) / 2;
    unsigned char *plaintext = malloc(plaintext_len);
    if (!plaintext) {
        fprintf(stderr, "Memory allocation failed.\n");
        return 1;
    }

    for (size_t i = 0; i < plaintext_len; i++) {
        sscanf(&plaintext_hex[i * 2], "%2hhx", &plaintext[i]);
    }

    size_t key_len = strlen(key_hex) / 2;
    unsigned char *key = malloc(key_len);
    if (!key) {
        fprintf(stderr, "Memory allocation failed.\n");
        free(plaintext);
        return 1;
    }

    for (size_t i = 0; i < key_len; i++) {
        sscanf(&key_hex[i * 2], "%2hhx", &key[i]);
    }

    unsigned char iv[AES_BLOCK_BYTES];
    if (!iv_hex) {
        memset(iv, 0, AES_BLOCK_BYTES);
    } else {
        size_t iv_len = strlen(iv_hex) / 2;
        for (size_t i = 0; i < iv_len && i < AES_BLOCK_BYTES; i++) {
            sscanf(&iv_hex[i * 2], "%2hhx", &iv[i]);
        }
    }

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "Failed to create cipher context.\n");
        free(plaintext);
        free(key);
        return 1;
    }

    unsigned char ciphertext[plaintext_len];
    size_t ciphertext_len = 0;

    if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_128_ctr(), NULL, key, iv)) {
        fprintf(stderr, "Encryption initialization failed.\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        return 1;
    }

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &ciphertext_len, plaintext, plaintext_len)) {
        fprintf(stderr, "Encryption update failed.\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        return 1;
    }

    size_t final_len = 0;
    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + ciphertext_len, &final_len)) {
        fprintf(stderr, "Encryption finalization failed.\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        return 1;
    }

    ciphertext_len += final_len;

    printf("密文: ");
    for (size_t i = 0; i < ciphertext_len; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    free(plaintext);
    free(key);

    return 0;
}