#include <iostream>
#include <string>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <vector>
#include <iomanip>
#include <sstream>

// DES implementation constants
static const int DES_BLOCK_SIZE = 8;
static const int DES_KEY_SIZE = 8;

// Initial Permutation table
static const int IP[64] = {
    58, 50, 42, 34, 26, 18, 10, 2,
    60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6,
    64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1,
    59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5,
    63, 55, 47, 39, 31, 23, 15, 7
};

// Final Permutation table (inverse of IP)
static const int FP[64] = {
    40, 8, 48, 16, 56, 24, 64, 32,
    39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30,
    37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28,
    35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26,
    33, 1, 41, 9, 49, 17, 57, 25
};

// Expansion table
static const int E[48] = {
    32, 1, 2, 3, 4, 5,
    4, 5, 6, 7, 8, 9,
    8, 9, 10, 11, 12, 13,
    12, 13, 14, 15, 16, 17,
    16, 17, 18, 19, 20, 21,
    20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29,
    28, 29, 30, 31, 32, 1
};

// S-boxes
static const int S[8][4][16] = {
    {
        {14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7},
        {0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8},
        {4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0},
        {15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13}
    },
    {
        {15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10},
        {3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5},
        {0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15},
        {13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9}
    },
    {
        {10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8},
        {13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1},
        {13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7},
        {1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12}
    },
    {
        {7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15},
        {13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9},
        {10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4},
        {3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14}
    },
    {
        {2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9},
        {14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6},
        {4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14},
        {11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3}
    },
    {
        {12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11},
        {10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8},
        {9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6},
        {4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13}
    },
    {
        {4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1},
        {13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6},
        {1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2},
        {6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12}
    },
    {
        {13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7},
        {1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2},
        {7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8},
        {2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11}
    }
};

// Permutation table P
static const int P[32] = {
    16,7,20,21,29,12,28,17,
    1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9,
    19,13,30,6,22,11,4,25
};

// PC-1 table for key schedule
static const int PC1[56] = {
    57,49,41,33,25,17,9,
    1,58,50,42,34,26,18,
    10,2,59,51,43,35,27,
    19,11,3,60,52,44,36,
    63,55,47,39,31,23,15,
    7,62,54,46,38,30,22,
    14,6,61,53,45,37,29,
    21,13,5,28,20,12,4
};

// PC-2 table for key schedule
static const int PC2[48] = {
    14,17,11,24,1,5,
    3,28,15,6,21,10,
    23,19,12,4,26,8,
    16,7,27,20,13,2,
    41,52,31,37,47,55,
    30,40,51,45,33,48,
    44,49,39,56,34,53,
    46,42,50,36,29,32
};

// Left shifts per round
static const int SHIFTS[16] = {1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1};

// Helper functions
static uint64_t hex_to_uint64(const std::string& hex) {
    return std::stoull(hex, nullptr, 16);
}

static std::string uint64_to_hex(uint64_t val) {
    std::stringstream ss;
    ss << std::hex << std::setfill('0') << std::setw(16) << val;
    return ss.str();
}

static uint64_t permute(uint64_t input, const int* table, int n) {
    uint64_t result = 0;
    for (int i = 0; i < n; i++) {
        result <<= 1;
        result |= (input >> (64 - table[i])) & 1;
    }
    return result;
}

static uint64_t des_encrypt_block(uint64_t block, const uint64_t subkeys[16]) {
    // Initial permutation
    block = permute(block, IP, 64);
    
    // Split into left and right halves
    uint32_t left = (block >> 32) & 0xFFFFFFFF;
    uint32_t right = block & 0xFFFFFFFF;
    
    // 16 rounds
    for (int round = 0; round < 16; round++) {
        // Expansion
        uint64_t expanded = permute(right, E, 48);
        
        // XOR with subkey
        expanded ^= subkeys[round];
        
        // S-box substitution
        uint32_t sbox_out = 0;
        for (int i = 0; i < 8; i++) {
            int row = ((expanded >> (42 - i*6)) & 0x20) | ((expanded >> (42 - i*6 + 5)) & 1);
            int col = (expanded >> (42 - i*6 + 1)) & 0x0F;
            sbox_out = (sbox_out << 4) | S[i][row][col];
        }
        
        // P permutation
        uint32_t p_out = permute(sbox_out, P, 32);
        
        // XOR with left half
        p_out ^= left;
        
        // Swap halves
        left = right;
        right = p_out;
    }
    
    // Final swap and permutation
    uint64_t combined = ((uint64_t)right << 32) | left;
    return permute(combined, FP, 64);
}

static void generate_subkeys(uint64_t key, uint64_t subkeys[16]) {
    // PC-1 permutation
    uint64_t permuted_key = permute(key, PC1, 56);
    
    // Split into C and D
    uint32_t C = (permuted_key >> 28) & 0x0FFFFFFF;
    uint32_t D = permuted_key & 0x0FFFFFFF;
    
    // Generate 16 subkeys
    for (int i = 0; i < 16; i++) {
        // Left shift
        C = ((C << SHIFTS[i]) | (C >> (28 - SHIFTS[i]))) & 0x0FFFFFFF;
        D = ((D << SHIFTS[i]) | (D >> (28 - SHIFTS[i]))) & 0x0FFFFFFF;
        
        // Combine and apply PC-2
        uint64_t combined = ((uint64_t)C << 28) | D;
        subkeys[i] = permute(combined, PC2, 48);
    }
}

static std::string hex_string_to_bytes(const std::string& hex) {
    std::string bytes;
    for (size_t i = 0; i < hex.length(); i += 2) {
        std::string byte_str = hex.substr(i, 2);
        char byte = static_cast<char>(std::stoi(byte_str, nullptr, 16));
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
    
    // Convert hex strings to binary data
    std::string plaintext = hex_string_to_bytes(plaintext_hex);
    uint64_t key = hex_to_uint64(key_hex);
    uint64_t iv = hex_to_uint64(iv_hex);
    
    // Generate subkeys
    uint64_t subkeys[16];
    generate_subkeys(key, subkeys);
    
    // CFB mode encryption
    std::vector<uint8_t> ciphertext;
    uint64_t feedback = iv;
    
    for (size_t i = 0; i < plaintext.length(); i += DES_BLOCK_SIZE) {
        // Encrypt the feedback
        uint64_t encrypted_feedback = des_encrypt_block(feedback, subkeys);
        
        // XOR with plaintext block
        uint64_t plaintext_block = 0;
        size_t block_size = std::min((size_t)DES_BLOCK_SIZE, plaintext.length() - i);
        for (size_t j = 0; j < block_size; j++) {
            plaintext_block = (plaintext_block << 8) | (unsigned char)plaintext[i + j];
        }
        
        uint64_t ciphertext_block = encrypted_feedback ^ plaintext_block;
        
        // Store ciphertext
        for (int j = DES_BLOCK_SIZE - 1; j >= 0; j--) {
            ciphertext.push_back((ciphertext_block >> (j * 8)) & 0xFF);
        }
        
        // Update feedback for next block
        feedback = ciphertext_block;
    }
    
    // Output ciphertext as hex
    for (size_t i = 0; i < ciphertext.size(); i++) {
        std::cout << std::hex << std::setfill('0') << std::setw(2) << (int)ciphertext[i];
    }
    std::cout << std::endl;
    
    return 0;
}