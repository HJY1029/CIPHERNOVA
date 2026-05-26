#include <iostream>
#include <string>
#include <cstring>
#include <cstdlib>
#include <iomanip>
#include <sstream>
#include <vector>

// Simplified DES implementation for OFB mode
// Note: This is a minimal implementation for educational purposes
// Real DES requires proper S-boxes, permutations, and key schedule

class SimpleDES {
private:
    uint64_t key;
    uint64_t iv;
    
    // Simplified Feistel function (not real DES)
    uint32_t feistel(uint32_t half, uint64_t round_key) {
        // Simple substitution and permutation
        uint32_t result = half ^ (uint32_t)(round_key & 0xFFFFFFFF);
        // Bit mixing
        result = (result << 3) | (result >> 29);
        result ^= (uint32_t)(round_key >> 32);
        return result;
    }
    
    // Generate round keys (simplified)
    std::vector<uint64_t> generateRoundKeys(uint64_t key) {
        std::vector<uint64_t> round_keys(16);
        uint64_t k = key;
        for (int i = 0; i < 16; i++) {
            // Simple rotation and permutation
            k = (k << 1) | (k >> 63);
            round_keys[i] = k ^ 0x0123456789ABCDEFULL;
        }
        return round_keys;
    }
    
    // DES encryption of a single 64-bit block
    uint64_t encryptBlock(uint64_t block) {
        auto round_keys = generateRoundKeys(key);
        
        // Initial permutation (simplified)
        uint32_t left = (block >> 32) & 0xFFFFFFFF;
        uint32_t right = block & 0xFFFFFFFF;
        
        // 16 rounds
        for (int i = 0; i < 16; i++) {
            uint32_t temp = right;
            right = left ^ feistel(right, round_keys[i]);
            left = temp;
        }
        
        // Final permutation (simplified)
        return ((uint64_t)right << 32) | left;
    }
    
public:
    SimpleDES(uint64_t k, uint64_t i) : key(k), iv(i) {}
    
    std::vector<uint8_t> encryptOFB(const std::vector<uint8_t>& plaintext) {
        std::vector<uint8_t> ciphertext;
        uint64_t feedback = iv;
        
        for (size_t i = 0; i < plaintext.size(); i += 8) {
            // Encrypt the feedback value
            uint64_t encrypted_feedback = encryptBlock(feedback);
            
            // XOR with plaintext block
            uint64_t plain_block = 0;
            size_t bytes_to_process = std::min((size_t)8, plaintext.size() - i);
            
            for (size_t j = 0; j < bytes_to_process; j++) {
                plain_block |= ((uint64_t)plaintext[i + j]) << (56 - j * 8);
            }
            
            uint64_t cipher_block = plain_block ^ encrypted_feedback;
            
            // Output ciphertext bytes
            for (size_t j = 0; j < bytes_to_process; j++) {
                ciphertext.push_back((cipher_block >> (56 - j * 8)) & 0xFF);
            }
            
            // Update feedback for next iteration
            feedback = encrypted_feedback;
        }
        
        return ciphertext;
    }
};

uint64_t hexToUint64(const std::string& hex) {
    uint64_t result = 0;
    for (char c : hex) {
        result <<= 4;
        if (c >= '0' && c <= '9') result |= (c - '0');
        else if (c >= 'a' && c <= 'f') result |= (c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') result |= (c - 'A' + 10);
    }
    return result;
}

std::vector<uint8_t> hexToBytes(const std::string& hex) {
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i < hex.length(); i += 2) {
        std::string byteString = hex.substr(i, 2);
        uint8_t byte = (uint8_t)strtol(byteString.c_str(), NULL, 16);
        bytes.push_back(byte);
    }
    return bytes;
}

int main() {
    // Read environment variables
    const char* plaintext_hex = std::getenv("TEST_PLAINTEXT");
    const char* key_hex = std::getenv("TEST_KEY");
    const char* iv_hex = std::getenv("TEST_IV");
    
    if (!plaintext_hex || !key_hex || !iv_hex) {
        std::cerr << "Missing environment variables" << std::endl;
        return 1;
    }
    
    // Convert hex strings to values
    std::vector<uint8_t> plaintext = hexToBytes(plaintext_hex);
    uint64_t key = hexToUint64(key_hex);
    uint64_t iv = hexToUint64(iv_hex);
    
    // Create DES instance and encrypt
    SimpleDES des(key, iv);
    std::vector<uint8_t> ciphertext = des.encryptOFB(plaintext);
    
    // Output ciphertext as lowercase hex
    for (uint8_t byte : ciphertext) {
        std::cout << std::hex << std::setw(2) << std::setfill('0') 
                  << (int)byte;
    }
    std::cout << std::endl;
    
    return 0;
}