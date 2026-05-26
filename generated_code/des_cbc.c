#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define DES_BLOCK_BYTES 8

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

int hex_to_bytes(const char *hex, unsigned char *bytes, size_t len) {
    for (size_t i = 0; i < len; i++) {
        if (sscanf(hex + 2 * i, "%2hhx", bytes + i) != 1) {
            return -1;
        }
    }
    return 0;
}

void des_encrypt(const unsigned char key[8], const unsigned char plaintext[DES_BLOCK_BYTES], unsigned char ciphertext[DES_BLOCK_BYTES]) {
    // 纯C DES加密算法实现
    // ...
}

int main() {
    if (getenv("TEST_IV") == NULL) {
        printf("请输入IV（十六进制）：");
        char input[100];
        fgets(input, sizeof(input), stdin);
        remove_whitespace(input);
        unsigned char iv[DES_BLOCK_BYTES];
        hex_to_bytes(input, iv, DES_BLOCK_BYTES);
    } else {
        unsigned char iv[DES_BLOCK_BYTES];
        hex_to_bytes(getenv("TEST_IV"), iv, DES_BLOCK_BYTES);
    }

    if (getenv("TEST_KEY") == NULL) {
        printf("请输入密钥（十六进制）：");
        char input[100];
        fgets(input, sizeof(input), stdin);
        remove_whitespace(input);
        unsigned char key[DES_BLOCK_BYTES];
        hex_to_bytes(input, key, DES_BLOCK_BYTES);
    } else {
        unsigned char key[DES_BLOCK_BYTES];
        hex_to_bytes(getenv("TEST_KEY"), key, DES_BLOCK_BYTES);
    }

    if (getenv("TEST_PLAINTEXT") == NULL) {
        printf("请输入明文（十六进制）：");
        char input[100];
        fgets(input, sizeof(input), stdin);
        remove_whitespace(input);
        unsigned char plaintext[DES_BLOCK_BYTES];
        hex_to_bytes(input, plaintext, DES_BLOCK_BYTES);
    } else {
        unsigned char plaintext[DES_BLOCK_BYTES];
        hex_to_bytes(getenv("TEST_PLAINTEXT"), plaintext, DES_BLOCK_BYTES);
    }

    size_t plaintext_len = strlen(plaintext) / 2;
    unsigned char *ciphertext = (unsigned char *)malloc(plaintext_len + DES_BLOCK_BYTES);

    des_encrypt(key, plaintext, ciphertext);

    printf("密文: ");
    for (size_t i = 0; i < plaintext_len; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    free(ciphertext);
    return 0;
}