/* Task: DES-CFB encrypt in C. Read TEST_PLAINTEXT, TEST_KEY, TEST_IV from env (hex). Print ciphertext hex. Complete the program below. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    char *ciphertext;
    char *key;
    char *iv;
    char *plaintext;

    printf("Enter the ciphertext: ");
    scanf("%s", &ciphertext);

    printf("Enter the key: ");
    scanf("%s", &key);

    printf("Enter the IV: ");
    scanf("%s", &iv);

    printf("Enter the plaintext: ");
    scanf("%s", &plaintext);

    printf("Encrypting the plaintext with the key: ");
    DES_cfb128_encrypt(plaintext, key, iv, ciphertext);

    printf("Encrypted the plaintext with the key: ");
    DES_cfb128_decrypt(ciphertext, key, iv, plaintext);

    return 0;
}
