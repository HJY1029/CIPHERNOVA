#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
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
        fprintf(stderr, "Missing environment variables\n");
        return 1;
    }

    remove_whitespace((char *)plaintext_hex);
    remove_whitespace((char *)key_hex);

    int plaintext_len = strlen(plaintext_hex) / 2;
    int key_len = strlen(key_hex) / 2;

    unsigned char *plaintext = (unsigned char *)malloc(plaintext_len);
    unsigned char *key = (unsigned char *)malloc(key_len);
    unsigned char *iv = (unsigned char *)malloc(AES_BLOCK_BYTES);

    for (int i = 0; i < plaintext_len; i++) {
        sscanf(&plaintext_hex[i*2], "%2hhx", &plaintext[i]);
    }
    for (int i = 0; i < key_len; i++) {
        sscanf(&key_hex[i*2], "%2hhx", &key[i]);
    }

    if (!iv_hex) {
        memset(iv, 0, AES_BLOCK_BYTES);
    } else {
        remove_whitespace((char *)iv_hex);
        for (int i = 0; i < AES_BLOCK_BYTES; i++) {
            sscanf(&iv_hex[i*2], "%2hhx", &iv[i]);
        }
    }

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "Failed to create cipher context\n");
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }

    int len;
    int ciphertext_len = 0;
    unsigned char *ciphertext = malloc(plaintext_len);

    if (key_len == 16) {
        EVP_EncryptInit_ex(ctx, EVP_aes_128_cfb8(), NULL, key, iv);
    } else if (key_len == 24) {
        EVP_EncryptInit_ex(ctx, EVP_aes_192_cfb8(), NULL, key, iv);
    } else if (key_len == 32) {
        EVP_EncryptInit_ex(ctx, EVP_aes_256_cfb8(), NULL, key, iv);
    } else {
        fprintf(stderr, "Invalid key length\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        return 1;
    }

    EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, plaintext_len);
    ciphertext_len = len;
    EVP_EncryptFinal_ex(ctx, ciphertext + len, &len);
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
    free(iv);

    return 0;
}