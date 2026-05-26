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
    const char *aad_hex = getenv("TEST_AAD");

    if (!plaintext_hex || !key_hex || !iv_hex || !aad_hex) {
        fprintf(stderr, "缺少环境变量\n");
        return 1;
    }

    // 去除空白字符
    remove_whitespace(plaintext_hex);
    remove_whitespace(key_hex);
    remove_whitespace(iv_hex);
    remove_whitespace(aad_hex);

    // 将十六进制字符串转换为字节数组
    unsigned char *plaintext = (unsigned char *)malloc(strlen(plaintext_hex) / 2);
    unsigned char *key = (unsigned char *)malloc(strlen(key_hex) / 2);
    unsigned char *iv = (unsigned char *)malloc(strlen(iv_hex) / 2);
    unsigned char *aad = (unsigned char *)malloc(strlen(aad_hex) / 2);

    for (int i = 0; i < strlen(plaintext_hex) / 2; i++) {
        sscanf(&plaintext_hex[i * 2], "%2hhx", &plaintext[i]);
        sscanf(&key_hex[i * 2], "%2hhx", &key[i]);
        sscanf(&iv_hex[i * 2], "%2hhx", &iv[i]);
        sscanf(&aad_hex[i * 2], "%2hhx", &aad[i]);
    }

    // 初始化 EVP 上下文
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "无法创建 EVP 上下文\n");
        free(plaintext);
        free(key);
        free(iv);
        free(aad);
        return 1;
    }

    // 设置 GCM 模式
    if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, key, iv)) {
        fprintf(stderr, "无法初始化加密\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        free(aad);
        return 1;
    }

    // 设置 AAD
    if (1 != EVP_EncryptUpdate(ctx, NULL, &aad_len, aad, strlen((char *)aad))) {
        fprintf(stderr, "无法更新 AAD\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        free(aad);
        return 1;
    }

    // 加密数据
    int len;
    if (1 != EVP_EncryptUpdate(ctx, plaintext, &len, plaintext, strlen((char *)plaintext))) {
        fprintf(stderr, "无法加密数据\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        free(aad);
        return 1;
    }

    // 获取密文长度
    int ciphertext_len = len;

    // 获取标签（tag）
    unsigned char tag[AES_BLOCK_BYTES];
    if (1 != EVP_EncryptFinal_ex(ctx, NULL, &len)) {
        fprintf(stderr, "无法完成加密\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        free(iv);
        free(aad);
        return 1;
    }

    // 获取标签长度
    int tag_len = len;

    // 打印密文和标签
    printf("密文: ");
    for (int i = 0; i < ciphertext_len + tag_len; i++) {
        printf("%02x", plaintext[i]);
    }
    printf("\n");

    // 清理资源
    EVP_CIPHER_CTX_free(ctx);
    free(plaintext);
    free(key);
    free(iv);
    free(aad);

    return 0;
}