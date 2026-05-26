#include <openssl/evp.h>
#include <cstring>

void h2b(const char* ph, unsigned char* out) {
    for (size_t i = 0; i < strlen(ph); i += 2) {
        sscanf(&ph[i], "%2hhx", &out[i / 2]);
    }
}

int main() {
    const char* ph = "874d6191b620e3261bef5b91b620e3261bef5b91b620e3261bef5b91";
    size_t len = strlen(ph) / 2;

    unsigned char* plaintext = new unsigned char[len];
    h2b(ph, plaintext);

    unsigned char key[16] = {0};
    unsigned char iv[16] = {0};

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        delete[] plaintext;
        return 1;
    }

    if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_128_ctr(), NULL, key, iv)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        return 1;
    }

    EVP_CIPHER_CTX_set_padding(ctx, 0);

    unsigned char* ciphertext = new unsigned char[len];
    int final_len;
    if (1 != EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, len) ||
        1 != EVP_EncryptFinal_ex(ctx, ciphertext + len, &final_len)) {
        EVP_CIPHER_CTX_free(ctx);
        delete[] plaintext;
        delete[] ciphertext;
        return 1;
    }

    len += final_len;

    printf("密文: ");
    for (int i = 0; i < len; ++i) {
        printf("%02x", ciphertext[i]);
    }
    printf("\n");

    EVP_CIPHER_CTX_free(ctx);
    delete[] plaintext;
    delete[] ciphertext;

    return 0;
}