#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DES_BLOCK_BYTES 8

void hex_to_bytes(const char *hex, unsigned char *bytes, size_t len) {
    for (size_t i = 0; i < len; i++) {
        bytes[i] = (hex[2 * i] - '0') * 16 + (hex[2 * i + 1] - '0');
    }
}

void des_encrypt(const unsigned char *plaintext, size_t plaintext_len, const unsigned char *key, unsigned char *ciphertext) {
    // DES加密算法实现
    // ...
}

int main() {
    const char *plaintext_hex = getenv("TEST_PLAINTEXT");
    const char *key_hex = getenv("TEST_KEY");

    if (!plaintext_hex || !key_hex) {
        fprintf(stderr, "环境变量未设置\n");
        return 1;
    }

    size_t plaintext_len = strlen(plaintext_hex);
    unsigned char *plaintext = (unsigned char *)malloc(plaintext_len / 2);
    hex_to_bytes(plaintext_hex, plaintext, plaintext_len / 2);

    size_t key_len = strlen(key_hex);
    unsigned char *key = (unsigned char *)malloc(key_len / 2);
    hex_to_bytes(key_hex, key, key_len / 2);

    unsigned char ciphertext[DES_BLOCK_BYTES];
    des_encrypt(plaintext, plaintext_len / 2, key, ciphertext);

    printf("密文: ");
    for (int i = 0; i < DES_BLOCK_BYTES; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    free(plaintext);
    free(key);

    return 0;
}