#include <openssl/rsa.h>
#include <openssl/pem.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <iostream>
#include <cstring>
#include <cstdlib>

// 将16进制字符串转换为unsigned char数组
void h2b(const std::string& hex, unsigned char* out) {
    for (size_t i = 0; i < hex.length(); i += 2) {
        out[i / 2] = (hex[i] >= '0' && hex[i] <= '9') ? (hex[i] - '0') : ((hex[i] & 0xF) + 9);
        out[i / 2] <<= 4;
        out[i / 2] |= (hex[i + 1] >= '0' && hex[i + 1] <= '9') ? (hex[i + 1] - '0') : ((hex[i + 1] & 0xF) + 9);
    }
}

// 将unsigned char数组转换为16进制字符串
void b2h(const unsigned char* in, size_t len, std::string& out) {
    for (size_t i = 0; i < len; ++i) {
        char buf[3];
        snprintf(buf, sizeof(buf), "%02x", in[i]);
        out += buf;
    }
}

int main() {
    // 获取环境变量
    const char* test_plaintext = std::getenv("TEST_PLAINTEXT");
    const char* test_ciphertext = std::getenv("TEST_CIPHERTEXT");
    const char* test_key = std::getenv("TEST_KEY");
    const char* test_iv = std::getenv("TEST_IV");

    // 获取RSA测试数据
    const char* test_public_key_n = std::getenv("TEST_PUBLIC_KEY_N");
    const char* test_public_key_e = std::getenv("TEST_PUBLIC_KEY_E");
    const char* test_private_key_n = std::getenv("TEST_PRIVATE_KEY_N");
    const char* test_private_key_d = std::getenv("TEST_PRIVATE_KEY_D");

    if (!test_plaintext || !test_ciphertext || !test_key || !test_iv ||
        !test_public_key_n || !test_public_key_e || !test_private_key_n || !test_private_key_d) {
        return 1;
    }

    // 将测试数据转换为unsigned char数组
    unsigned char plaintext[16];
    h2b(test_plaintext, plaintext);

    unsigned char ciphertext[256];
    size_t ciphertext_len = sizeof(ciphertext);

    RSA* rsa_pub = RSA_new();
    BIGNUM* bn_n = BN_new();
    BIGNUM* bn_e = BN_new();
    BN_hex2bn(&bn_n, test_public_key_n);
    BN_hex2bn(&bn_e, test_public_key_e);
    RSA_set0_modulus(rsa_pub, BN_dup(bn_n));
    RSA_set0_exponent(rsa_pub, BN_dup(bn_e));

    // 加密
    if (RSA_public_encrypt(16, plaintext, ciphertext, rsa_pub, RSA_PKCS1_PADDING) == -1) {
        std::cerr << "Encryption failed" << std::endl;
        return 1;
    }

    // 将加密结果转换为16进制字符串并输出
    std::string hex_ciphertext;
    b2h(ciphertext, ciphertext_len, hex_ciphertext);
    std::cout << "密文: " << hex_ciphertext << std::endl;

    RSA_free(rsa_pub);

    return 0;
}