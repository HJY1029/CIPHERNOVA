#include <stdio.h>
#include <stdlib.h>
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

int h2b(const char *hex, unsigned char *bin, size_t bin_len) {
    for (size_t i = 0; i < bin_len; i++) {
        sscanf(hex + i * 2, "%2hhx", &bin[i]);
    }
    return 0;
}

void aes_cbc_encrypt(const unsigned char *plaintext, size_t plaintext_len,
                     const unsigned char *key, const unsigned char *iv,
                     unsigned char *ciphertext) {
    // 实现AES CBC模式加密
    // 使用EVP_aes_128_cbc()和EVP_CIPHER_CTX_set_padding(ctx, 0)
}

int main(int argc, char *argv[]) {
    if (argc < 4) {
        fprintf(stderr, "Usage: %s <plaintext> <key> <iv>\n", argv[0]);
        return 1;
    }

    const char *plaintext_hex = argv[1];
    const char *key_hex = argv[2];
    const char *iv_hex = argv[3];

    unsigned char plaintext[AES_BLOCK_BYTES * 4]; // 假设最大长度为4块
    unsigned char key[AES_BLOCK_BYTES];
    unsigned char iv[AES_BLOCK_BYTES];
    unsigned char ciphertext[AES_BLOCK_BYTES * 4];

    remove_whitespace((char *)plaintext_hex);
    remove_whitespace((char *)key_hex);
    remove_whitespace((char *)iv_hex);

    h2b(plaintext_hex, plaintext, AES_BLOCK_BYTES * 4);
    h2b(key_hex, key, AES_BLOCK_BYTES);
    h2b(iv_hex, iv, AES_BLOCK_BYTES);

    aes_cbc_encrypt(plaintext, strlen((const char *)plaintext), key, iv, ciphertext);

    printf("密文: ");
    for (size_t i = 0; i < strlen((const char *)ciphertext); i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    return 0;
}