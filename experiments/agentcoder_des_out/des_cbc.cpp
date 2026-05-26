#include <iostream>
#include <string>
#include <vector>
#include <cstring>
#include <cstdlib>
#include <iomanip>
#include <sstream>
#include <algorithm>

using namespace std;

// DES constants
const int IP[64] = {
    58,50,42,34,26,18,10,2,
    60,52,44,36,28,20,12,4,
    62,54,46,38,30,22,14,6,
    64,56,48,40,32,24,16,8,
    57,49,41,33,25,17,9,1,
    59,51,43,35,27,19,11,3,
    61,53,45,37,29,21,13,5,
    63,55,47,39,31,23,15,7
};

const int IP_INV[64] = {
    40,8,48,16,56,24,64,32,
    39,7,47,15,55,23,63,31,
    38,6,46,14,54,22,62,30,
    37,5,45,13,53,21,61,29,
    36,4,44,12,52,20,60,28,
    35,3,43,11,51,19,59,27,
    34,2,42,10,50,18,58,26,
    33,1,41,9,49,17,57,25
};

const int E[48] = {
    32,1,2,3,4,5,
    4,5,6,7,8,9,
    8,9,10,11,12,13,
    12,13,14,15,16,17,
    16,17,18,19,20,21,
    20,21,22,23,24,25,
    24,25,26,27,28,29,
    28,29,30,31,32,1
};

const int S[8][4][16] = {
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

const int P[32] = {
    16,7,20,21,29,12,28,17,
    1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9,
    19,13,30,6,22,11,4,25
};

const int PC1[56] = {
    57,49,41,33,25,17,9,
    1,58,50,42,34,26,18,
    10,2,59,51,43,35,27,
    19,11,3,60,52,44,36,
    63,55,47,39,31,23,15,
    7,62,54,46,38,30,22,
    14,6,61,53,45,37,29,
    21,13,5,28,20,12,4
};

const int PC2[48] = {
    14,17,11,24,1,5,
    3,28,15,6,21,10,
    23,19,12,4,26,8,
    16,7,27,20,13,2,
    41,52,31,37,47,55,
    30,40,51,45,33,48,
    44,49,39,56,34,53,
    46,42,50,36,29,32
};

const int SHIFT_SCHEDULE[16] = {1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1};

// Helper: hex string to bytes
vector<uint8_t> hex_to_bytes(const string& hex) {
    vector<uint8_t> bytes;
    for (size_t i = 0; i < hex.length(); i += 2) {
        string byte_str = hex.substr(i, 2);
        uint8_t byte = (uint8_t)strtol(byte_str.c_str(), nullptr, 16);
        bytes.push_back(byte);
    }
    return bytes;
}

// Helper: bytes to hex string
string bytes_to_hex(const vector<uint8_t>& bytes) {
    stringstream ss;
    for (uint8_t b : bytes) {
        ss << hex << setw(2) << setfill('0') << (int)b;
    }
    return ss.str();
}

// Permute bits according to table
uint64_t permute(uint64_t input, const int* table, int n) {
    uint64_t result = 0;
    for (int i = 0; i < n; i++) {
        result <<= 1;
        result |= (input >> (64 - table[i])) & 1;
    }
    return result;
}

// Rotate left
uint32_t rotate_left(uint32_t value, int shifts) {
    return ((value << shifts) | (value >> (28 - shifts))) & 0x0FFFFFFF;
}

// Generate subkeys
void generate_subkeys(uint64_t key, uint64_t subkeys[16]) {
    uint64_t perm_key = permute(key, PC1, 56);
    uint32_t C = (perm_key >> 28) & 0x0FFFFFFF;
    uint32_t D = perm_key & 0x0FFFFFFF;

    for (int round = 0; round < 16; round++) {
        C = rotate_left(C, SHIFT_SCHEDULE[round]);
        D = rotate_left(D, SHIFT_SCHEDULE[round]);
        uint64_t CD = ((uint64_t)C << 28) | D;
        subkeys[round] = permute(CD, PC2, 48);
    }
}

// DES round function f(R, K)
uint32_t f(uint32_t R, uint64_t K) {
    uint64_t expanded = 0;
    for (int i = 0; i < 48; i++) {
        expanded <<= 1;
        expanded |= (R >> (32 - E[i])) & 1;
    }
    expanded ^= K;

    uint32_t sbox_out = 0;
    for (int i = 0; i < 8; i++) {
        uint8_t block = (expanded >> (42 - 6*i)) & 0x3F;
        uint8_t row = ((block & 0x20) >> 4) | (block & 1);
        uint8_t col = (block >> 1) & 0x0F;
        uint8_t val = S[i][row][col];
        sbox_out = (sbox_out << 4) | val;
    }

    uint32_t pbox_out = 0;
    for (int i = 0; i < 32; i++) {
        pbox_out <<= 1;
        pbox_out |= (sbox_out >> (32 - P[i])) & 1;
    }
    return pbox_out;
}

// Encrypt one 64-bit block
uint64_t des_encrypt_block(uint64_t block, uint64_t subkeys[16]) {
    block = permute(block, IP, 64);
    uint32_t L = (block >> 32) & 0xFFFFFFFF;
    uint32_t R = block & 0xFFFFFFFF;

    for (int round = 0; round < 16; round++) {
        uint32_t temp = R;
        R = L ^ f(R, subkeys[round]);
        L = temp;
    }

    uint64_t pre_output = ((uint64_t)R << 32) | L;
    return permute(pre_output, IP_INV, 64);
}

// Decrypt one 64-bit block
uint64_t des_decrypt_block(uint64_t block, uint64_t subkeys[16]) {
    block = permute(block, IP, 64);
    uint32_t L = (block >> 32) & 0xFFFFFFFF;
    uint32_t R = block & 0xFFFFFFFF;

    for (int round = 15; round >= 0; round--) {
        uint32_t temp = R;
        R = L ^ f(R, subkeys[round]);
        L = temp;
    }

    uint64_t pre_output = ((uint64_t)R << 32) | L;
    return permute(pre_output, IP_INV, 64);
}

// CBC encrypt
vector<uint8_t> des_cbc_encrypt(const vector<uint8_t>& plaintext, const vector<uint8_t>& key_bytes, const vector<uint8_t>& iv_bytes) {
    uint64_t key = 0;
    for (int i = 0; i < 8; i++) key = (key << 8) | key_bytes[i];

    uint64_t iv = 0;
    for (int i = 0; i < 8; i++) iv = (iv << 8) | iv_bytes[i];

    uint64_t subkeys[16];
    generate_subkeys(key, subkeys);

    // PKCS#7 padding
    vector<uint8_t> padded = plaintext;
    size_t pad_len = 8 - (plaintext.size() % 8);
    for (size_t i = 0; i < pad_len; i++) padded.push_back((uint8_t)pad_len);

    vector<uint8_t> ciphertext;
    uint64_t prev = iv;

    for (size_t i = 0; i < padded.size(); i += 8) {
        uint64_t block = 0;
        for (int j = 0; j < 8; j++) block = (block << 8) | padded[i + j];
        block ^= prev;
        uint64_t enc = des_encrypt_block(block, subkeys);
        prev = enc;
        for (int j = 0; j < 8; j++) ciphertext.push_back((enc >> (56 - 8*j)) & 0xFF);
    }

    return ciphertext;
}

int main() {
    const char* plaintext_hex = getenv("TEST_PLAINTEXT");
    const char* key_hex = getenv("TEST_KEY");
    const char* iv_hex = getenv("TEST_IV");

    if (!plaintext_hex || !key_hex || !iv_hex) {
        cerr << "Missing environment variables" << endl;
        return 1;
    }

    vector<uint8_t> plaintext = hex_to_bytes(plaintext_hex);
    vector<uint8_t> key = hex_to_bytes(key_hex);
    vector<uint8_t> iv = hex_to_bytes(iv_hex);

    vector<uint8_t> ciphertext = des_cbc_encrypt(plaintext, key, iv);

    cout << bytes_to_hex(ciphertext) << endl;

    return 0;
}