#include <openssl/evp.h>
#include <stdio.h>
#include <string.h>

void remove_whitespace(char *str) {
    char *src = str, *dst = str;
    while (*src != '\0') {
        if (!isspace((unsigned char)*src)) {
            *dst++ = *src;
        }
        src++;
    }
    *dst = '\0';
}

unsigned char* h2b(const char* hex_str) {
    size_t len = strlen(hex_str);
    unsigned char* result = (unsigned char*)malloc(len / 2);
    for (size_t i = 0; i < len; i += 2) {
        sscanf(&hex_str[i], "%2hhx", &result[i / 2]);
    }
    return result;
}

int main() {
    // 从环境变量读取输入
    const char* test_plaintext = getenv("TEST_PLAINTEXT");
    if (!test_plaintext) {
        printf("请输入明文: ");
        fgets(test_plaintext, 1024, stdin);
        remove_whitespace((char*)test_plaintext);
    }

    const char* test_key = getenv("TEST_KEY");
    if (!test_key) {
        printf("请输入密钥: ");
        fgets(test_key, 32, stdin);
        remove_whitespace((char*)test_key);
    }

    unsigned char* plaintext = h2b(test_plaintext);
    unsigned char* key = h2b(test_key);

    // 初始化反馈寄存器（IV）
    const char* test_iv = getenv("TEST_IV");
    if (!test_iv) {
        printf("请输入 IV: ");
        fgets(test_iv, 16, stdin);
        remove_whitespace((char*)test_iv);
    }
    unsigned char iv[16];
    memcpy(iv, h2b(test_iv), 16);

    // 创建加密上下文
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        fprintf(stderr, "无法创建加密上下文\n");
        return 1;
    }

    // 初始化 SM4-OFB 加密
    if (1 != EVP_EncryptInit_ex(ctx, EVP_sm4_ofb(), NULL, key, iv)) {
        fprintf(stderr, "初始化加密失败\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        return 1;
    }

    // 设置填充为0
    EVP_CIPHER_CTX_set_padding(ctx, 0);

    // 加密数据
    unsigned char ciphertext[2 * strlen((char*)plaintext)];
    int ciphertext_len = 0;

    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &ciphertext_len, plaintext, strlen((char*)plaintext))) {
        fprintf(stderr, "加密更新失败\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        return 1;
    }

    int final_len = 0;
    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + ciphertext_len, &final_len)) {
        fprintf(stderr, "加密最终失败\n");
        EVP_CIPHER_CTX_free(ctx);
        free(plaintext);
        free(key);
        return 1;
    }

    ciphertext_len += final_len;

    // 输出密文
    printf("密文: ");
    for (int i = 0; i < ciphertext_len; i++) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    // 清理
    EVP_CIPHER_CTX_free(ctx);
    free(plaintext);
    free(key);

    return 0;
}