// Task: DES-CFB encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY, TEST_IV from env (hex). Print ciphertext hex.
#include <iostream>
#include <string>

int main() {
    std::string plaintext;
    std::string ciphertext;
    std::string key;
    std::string iv;

    std::cout << "Enter plaintext: ";
    std::cin >> plaintext;

    std::cout << "Enter ciphertext: ";
    std::cin >> ciphertext;

    std::cout << "Enter key: ";
    std::cin >> key;

    std::cout << "Enter iv: ";
    std::cin >> iv;

    std::cout << "Encrypting plaintext with key: " << key << std::endl;
    std::cout << "Encrypting ciphertext with key: " << key << std::endl;

    std::cout << "Encrypting plaintext with iv: " << iv << std::endl;
    std::cout << "Encrypting ciphertext with iv: " << iv << std::endl;

    std::cout << "Encrypting plaintext with key: " << plaintext << std::endl;
    std::cout << "Encrypting ciphertext with key: " << ciphertext << std::endl;

    std::cout << "Encrypting plaintext with iv: " << iv << std::endl;
    std::cout