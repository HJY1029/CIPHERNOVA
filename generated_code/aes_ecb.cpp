#include <openssl/evp.h>
#include <cstring>

void h2b(const char* hex, unsigned char* out) {
    for (int i = 0; i < strlen(hex); i += 2) {
        sscanf(&hex[i], "%2hhx", &out[i / 2]);
    }
}

int main() {
    const char* plaintext_hex = "db727ac6624f3699cbfc4f0f890832b8a4b1dca1f52ef8e4ce0fd12e307476c6";
    unsigned char plaintext[32];
    h2b(plaintext_hex, plaintext);

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;

    if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_128_ecb(), NULL, NULL, NULL)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }

    unsigned char ciphertext[32];
    int len;
    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, 32)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    int ciphertext_len = len;

    if (1 != EVP_EncryptFinal_ex(ctx, ciphertext + len, &len)) {
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    ciphertext_len += len;

    EVP_CIPHER_CTX_free(ctx);

    printf("密文: ");
    for (int i = 0; i < ciphertext_len; ++i) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    return 0;
}