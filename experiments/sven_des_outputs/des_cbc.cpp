// Task: DES-CBC encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY, TEST_IV from env (hex). Print ciphertext hex.
#include <iostream>
#include <string>

int main() {
    std::string plaintext, ciphertext;
    std::string key, iv;
    std::string test_plaintext;
    std::string test_ciphertext;

    // Read TEST_PLAINTEXT from env (hex).
    std::stringstream ss;
    ss << std::hex << TEST_PLAINTEXT;
    plaintext = ss.str();

    // Read TEST_KEY from env (hex).
    std::stringstream ss2;
    ss2 << std::hex << TEST_KEY;
    key = ss2.str();

    // Read TEST_IV from env (hex).
    std::stringstream ss3;
    ss3 << std::hex << TEST_IV;
    iv = ss3.str();

    // Decrypt TEST_PLAINTEXT.
    ciphertext = decrypt(plaintext, key, iv);

    // Print ciphertext hex.
    std::cout << ciphertext << std::endl;

    // Decrypt TEST_PLAINTEXT.
    ciphertext = decrypt(ciphertext, key, iv);

    // Print ciphertext hex.
    std::cout << ciphertext << std::endl;

    return 0;
}
