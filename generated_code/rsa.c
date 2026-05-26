#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <openssl/evp.h>

static void strip(char*s){char*d=s;while(*s){if(!isspace((unsigned char)*s))*d++=*s;s++;}*d=0;}
static int h2b(const char*h,unsigned char*b,int n){
    for(int i=0;i<n;i++){unsigned v;if(sscanf(h+2*i,"%2x",&v)!=1)return -1;b[i]=(unsigned char)v;}return 0;}
}


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
    const char *plaintext_env = getenv("TEST_PLAINTEXT");
    const char *public_key_n_env = getenv("TEST_PUBLIC_KEY_N");
    const char *public_key_e_env = getenv("TEST_PUBLIC_KEY_E");

    if (!plaintext_env || !public_key_n_env || !public_key_e_env) {
        fprintf(stderr, "Environment variables not set\n");
        return 1;
    }

    remove_whitespace((char *)plaintext_env);
    remove_whitespace((char *)public_key_n_env);
    remove_whitespace((char *)public_key_e_env);

    EVP_PKEY *pkey = NULL;
    BIGNUM *n = BN_new();
    BIGNUM *e = BN_new();

    if (!BN_hex2bn(&n, public_key_n_env) || !BN_hex2bn(&e, public_key_e_env)) {
        fprintf(stderr, "Invalid key format\n");
        return 1;
    }

    RSA *rsa = RSA_new();
    rsa->n = n;
    rsa->e = e;

    pkey = EVP_PKEY_new_from_rsa(rsa);

    if (!pkey) {
        fprintf(stderr, "Failed to create EVP_PKEY\n");
        BN_free(n);
        BN_free(e);
        return 1;
    }

    unsigned char *plaintext = (unsigned char *)malloc(strlen(plaintext_env) / 2 + 1);
    size_t plaintext_len = strlen(plaintext_env) / 2;

    if (!h2b(plaintext_env, plaintext, &plaintext_len)) {
        fprintf(stderr, "Failed to decode hex\n");
        EVP_PKEY_free(pkey);
        free(plaintext);
        return 1;
    }

    unsigned char *ciphertext = (unsigned char *)malloc(RSA_size(rsa));
    size_t ciphertext_len;

    if (!EVP_EncryptInit_ex(NULL, EVP_rsa_2048(), NULL, NULL, NULL) ||
        !EVP_EncryptUpdate(NULL, NULL, &ciphertext_len, plaintext, plaintext_len) ||
        !EVP_EncryptFinal_ex(NULL, ciphertext + ciphertext_len, &ciphertext_len)) {
        fprintf(stderr, "Encryption failed\n");
        EVP_PKEY_free(pkey);
        free(plaintext);
        free(ciphertext);
        return 1;
    }

    printf("密文: %s\n", bin2hex(ciphertext, ciphertext_len));

    EVP_PKEY_free(pkey);
    free(plaintext);
    free(ciphertext);

    return 0;
}