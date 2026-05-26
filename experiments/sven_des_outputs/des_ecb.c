/* Task: DES-ECB encrypt in C. Read TEST_PLAINTEXT, TEST_KEY from env (hex). Print ciphertext hex. Complete the program below. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    char *test_plaintext = "This is a test string";
    char *test_key = "This is a test key";
    char *test_ciphertext = "This is a test ciphertext";
    
    char *ciphertext = des_ecb_encrypt(test_ciphertext, test_key);
    printf("%s\n", ciphertext);
    
    return 0;
}
