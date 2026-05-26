// Task: DES-OFB encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY, TEST_IV from env (hex). Print ciphertext hex.
#include <iostream>
#include <string>

int main() {
  std::string plaintext;
  std::string ciphertext;
  std::string key;
  std::string iv;
  std::string test_plaintext;
  std::string test_ciphertext;

  // Task: DES-OFB encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY, TEST_IV from env (hex). Print ciphertext hex.
  std::cout << "TEST_PLAINTEXT: ";
  std::cin >> plaintext;
  std::cout << "TEST_KEY: ";
  std::cin >> key;
  std::cout << "TEST_IV: ";
  std::cin >> iv;
  std::cout << "TEST_CIPHERTEXT: ";
  std::cin >> ciphertext;

  // Task: DES-OFB encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY, TEST_IV from env (hex). Print ciphertext hex.
  std::cout << "TEST_PLAINTEXT: ";
  std::cout << plaintext << std::endl;
  std::cout << "TEST_KEY: ";
  std::cout << key << std::endl;
  std::cout << "TEST_IV: "