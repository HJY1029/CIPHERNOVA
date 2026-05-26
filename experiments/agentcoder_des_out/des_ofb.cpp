#include <iostream>
#include <string>
#include <vector>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <iomanip>
#include <sstream>

// DES constants
static const int IP[64] = {
    58,50,42,34,26,18,10,2,
    60,52,44,36,28,20,12,4,
    62,54,46,38,30,22,14,6,
    64,56,48,40,32,24,16,8,
    57,49,41,33,25,17,9,1,
    59,51,43,35,27,19,11,3,
    61,53,45,37,29,21,13,5,
    63,55,47,39,31,23,15,7
};

static const int FP[64] = {
    40,8,48,16,56,24,64,32,
    39,7,47,15,55,23,63,31,
    38,6,46,14,54,22,62,30,
    37,5,45,13,53,21,61,29,
    36,4,44,12,52,20,60,28,
    35,3,43,11,51,19,59,27,
    34,2,42,10,50,18,58,26,
    33,1,41,9,49,17,57,25
};

static const int E[48] = {
    32,1,2,3,4,5,
    4,5,6,7,8,9,
    8,9,10,11,12,13,
    12,13,14,15,16,17,
    16,17,18,19,20,21,
    20,21,22,23,24,25,
    24,25,26,27,28,29,
    28,29,30,31,32,1
};

static const int S[8][4][16] = {
    { {14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7},
      {0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8},
      {4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0},
      {15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13} },
    { {15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10},
      {3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5},
      {0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15},
      {13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9} },
    { {10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8},
      {13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1},
      {13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7},
      {1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12} },
    { {7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15},
      {13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9},
      {10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4},
      {3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14} },
    { {2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9},
      {14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6},
      {4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14},
      {11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3} },
    { {12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11},
      {10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8},
      {9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6},
      {4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13} },
    { {4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1},
      {13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6},
      {1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2},
      {6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12} },
    { {13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7},
      {1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2},
      {7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8},
      {2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11} }
};

static const int P[32] = {
    16,7,20,21,29,12,28,17,
    1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9,
    19,13,30,6,22,11,4,25
};

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

static const int SHIFT[16] = {1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1};

// Helper: rotate left 28-bit
uint32_t rotl28(uint32_t val, int bits) {
    return ((val << bits) | (val >> (28 - bits))) & 0x0FFFFFFF;
}

// Generate 16 subkeys from 64-bit key (with parity bits)
void generate_subkeys(uint64_t key, uint64_t subkeys[16]) {
    // Convert to 56-bit via PC1
    uint64_t perm_key = 0;
    for (int i = 0; i < 56; i++) {
        perm_key <<= 1;
        perm_key |= (key >> (64 - PC1[i])) & 1;
    }
    uint32_t C = (perm_key >> 28) & 0x0FFFFFFF;
    uint32_t D = perm_key & 0x0FFFFFFF;

    for (int round = 0; round < 16; round++) {
        C = rotl28(C, SHIFT[round]);
        D = rotl28(D, SHIFT[round]);
        uint64_t combined = ((uint64_t)C << 28) | D;
        uint64_t subkey = 0;
        for (int i = 0; i < 48; i++) {
            subkey <<= 1;
            subkey |= (combined >> (56 - PC2[i])) & 1;
        }
        subkeys[round] = subkey;
    }
}

// DES round function f(R, K) -> 32-bit
uint32_t f(uint32_t R, uint64_t K) {
    // Expansion E
    uint64_t expanded = 0;
    for (int i = 0; i < 48; i++) {
        expanded <<= 1;
        expanded |= (R >> (32 - E[i])) & 1;
    }
    // XOR with subkey
    expanded ^= K;
    // S-box substitution
    uint32_t output = 0;
    for (int i = 0; i < 8; i++) {
        int row = ((expanded >> (42 - i*6)) & 0x20) | ((expanded >> (42 - i*6)) & 1);
        int col = (expanded >> (43 - i*6)) & 0x0F;
        uint8_t sval = S[i][row][col];
        output = (output << 4) | sval;
    }
    // Permutation P
    uint32_t perm_out = 0;
    for (int i = 0; i < 32; i++) {
        perm_out <<= 1;
        perm_out |= (output >> (32 - P[i])) & 1;
    }
    return perm_out;
}

// Encrypt one 64-bit block
uint64_t des_encrypt_block(uint64_t block, uint64_t subkeys[16]) {
    // Initial permutation
    uint64_t ip_block = 0;
    for (int i = 0; i < 64; i++) {
        ip_block <<= 1;
        ip_block |= (block >> (64 - IP[i])) & 1;
    }
    uint32_t L = (ip_block >> 32) & 0xFFFFFFFF;
    uint32_t R = ip_block & 0xFFFFFFFF;

    for (int round = 0; round < 16; round++) {
        uint32_t newL = R;
        uint32_t newR = L ^ f(R, subkeys[round]);
        L = newL;
        R = newR;
    }

    uint64_t pre_output = ((uint64_t)R << 32) | L;
    // Final permutation
    uint64_t cipher = 0;
    for (int i = 0; i < 64; i++) {
        cipher <<= 1;
        cipher |= (pre_output >> (64 - FP[i])) & 1;
    }
    return cipher;
}

// Convert hex string to bytes
std::vector<uint8_t> hex_to_bytes(const std::string& hex) {
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i < hex.length(); i += 2) {
        std::string byte_str = hex.substr(i, 2);
        uint8_t byte = (uint8_t)strtol(byte_str.c_str(), nullptr, 16);
        bytes.push_back(byte);
    }
    return bytes;
}

// Convert bytes to hex string
std::string bytes_to_hex(const std::vector<uint8_t>& bytes) {
    std::ostringstream oss;
    for (uint8_t b : bytes) {
        oss << std::hex << std::setw(2) << std::setfill('0') << (int)b;
    }
    return oss.str();
}

// DES OFB encryption
std::vector<uint8_t> des_ofb_encrypt(const std::vector<uint8_t>& plaintext,
                                     const std::vector<uint8_t>& key_bytes,
                                     const std::vector<uint8_t>& iv_bytes) {
    // Convert key to 64-bit block (8 bytes)
    uint64_t key = 0;
    for (int i = 0; i < 8; i++) key = (key << 8) | key_bytes[i];
    uint64_t iv = 0;
    for (int i = 0; i < 8; i++) iv = (iv << 8) | iv_bytes[i];

    uint64_t subkeys[16];
    generate_subkeys(key, subkeys);

    std::vector<uint8_t> ciphertext;
    uint64_t feedback = iv;

    size_t blocks = plaintext.size() / 8;
    for (size_t b = 0; b < blocks; b++) {
        feedback = des_encrypt_block(feedback, subkeys);
        uint64_t plain_block = 0;
        for (int i = 0; i < 8; i++) plain_block = (plain_block << 8) | plaintext[b*8 + i];
        uint64_t cipher_block = plain_block ^ feedback;
        for (int i = 7; i >= 0; i--) ciphertext.push_back((cipher_block >> (i*8)) & 0xFF);
    }

    // Handle remaining bytes (partial block)
    size_t remaining = plaintext.size() % 8;
    if (remaining > 0) {
        feedback = des_encrypt_block(feedback, subkeys);
        for (size_t i = 0; i < remaining; i++) {
            ciphertext.push_back(plaintext[blocks*8 + i] ^ ((feedback >> (56 - i*8)) & 0xFF));
        }
    }

    return ciphertext;
}

int main() {
    const char* env_pt = std::getenv("TEST_PLAINTEXT");
    const char* env_key = std::getenv("TEST_KEY");
    const char* env_iv = std::getenv("TEST_IV");

    if (!env_pt || !env_key || !env_iv) {
        std::cerr << "Missing environment variables" << std::endl;
        return 1;
    }

    std::string pt_hex(env_pt);
    std::string key_hex(env_key);
    std::string iv_hex(env_iv);

    std::vector<uint8_t> plaintext = hex_to_bytes(pt_hex);
    std::vector<uint8_t> key_bytes = hex_to_bytes(key_hex);
    std::vector<uint8_t> iv_bytes = hex_to_bytes(iv_hex);

    std::vector<uint8_t> ciphertext = des_ofb_encrypt(plaintext, key_bytes, iv_bytes);

    std::cout << bytes_to_hex(ciphertext) << std::endl;

    return 0;
}