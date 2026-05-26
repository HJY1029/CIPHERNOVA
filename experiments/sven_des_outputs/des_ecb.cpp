// Task: DES-ECB encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY from env (hex). Print ciphertext hex.
#include <iostream>
#include <string>

int main() {
    std::string plaintext;
    std::string ciphertext;
    std::string key;
    std::string test_key;
    std::string test_plaintext;

    std::string test_hex = "0123456789ABCDEF";

    std::cout << "TEST_PLAINTEXT: " << test_plaintext << std::endl;
    std::cout << "TEST_KEY: " << test_key << std::endl;

    std::cout << "TEST_HEX: " << test_hex << std::endl;

    std::cout << "Plaintext: " << plaintext << std::endl;
    std::cout << "Ciphertext: " << ciphertext << std::endl;
    std::cout << "Key: " << key << std::endl;
    std::cout << "Test_Key: " << test_key << std::endl;

    std::cout << "Test_HEX: " << test_hex << std::endl;

    std::cout << "Plaintext: " << plaintext << std::endl;
    std::cout << "Ciphertext: " << ciphertext << std::endl;
    std::cout << "Key: "