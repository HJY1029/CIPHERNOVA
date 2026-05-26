```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

// DES S-boxes
static const uint8_t sbox[8][4][16] = {
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

// Permutation tables
static const uint8_t IP[64] = {
    58,50,42,34,26,18,10,2,60,52,44,36,28,20,12,4,
    62,54,46,38,30,22,14,6,64,56,48,40,32,24,16,8,
    57,49,41,33,25,17,9,1,59,51,43,35,27,19,11,3,
    61,53,45,37,29,21,13,5,63,55,47,39,31,23,15,7
};

static const uint8_t IPinv[64] = {
    40,8,48,16,56,24,64,32,39,7,47,15,55,23,63,31,
    38,6,46,14,54,22,62,30,37,5,45,13,53,21,61,29,
    36,4,44,12,52,20,60,28,35,3,43,11,51,19,59,27,
    34,2,42,10,50,18,58,26,33,1,41,9,49,17,57,25
};

static const uint8_t E[48] = {
    32,1,2,3,4,5,4,5,6,7,8,9,8,9,10,11,
    12,13,12,13,14,15,16,17,16,17,18,19,20,21,
    20,21,22,23,24,25,24,25,26,27,28,29,28,29,
    30,31,32,1
};

static const uint8_t P[32] = {
    16,7,20,21,29,12,28,17,1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9,19,13,30,6,22,11,4,25
};

static const uint8_t PC1[56] = {
    57,49,41,33,25,17,9,1,58,50,42,34,26,18,10,2,
    59,51,43,35,27,19,11,3,60,52,44,36,63,55,47,39,
    31,23,15,7,62,54,46,38,30,22,14,6,61,53,45,37,
    29,21,13,5,28,20,12,4
};

static const uint8_t PC2[48] = {
    14,17,11,24,1,5,3,28,15,6,21,10,23,19,12,4,
    26,8,16,7,27,20,13,2,41,52,31,37,47,55,30,40,
    51,45,33,48,44,49,39,56,34,53,46,42,50,36,29,32
};

static const uint8_t shifts[16] = {1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1};

// Helper: hex char to nibble
static uint8_t hex2nib(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return 0;
}

// Helper: hex string to bytes (len in bytes)
static void hex2bytes(const char *hex, uint8_t *out, size_t len) {
    for (size_t i = 0; i < len; i++) {
        out[i] = (hex2nib(hex[2*i]) << 4) | hex2nib(hex[2*i+1]);
    }
}

// Helper: bytes to hex string (lowercase)
static void bytes2hex(const uint8_t *in, char *out, size_t len) {
    const char *hex = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        out[2*i] = hex[in[i] >> 4];
        out[2*i+1] = hex[in[i] & 0x0f];
    }
    out[2*len] = '\0';
}

// Key schedule: generate 16 round keys (48 bits each)
static void key_schedule(const uint8_t *key, uint8_t round_keys[16][6]) {
    uint8_t perm_key[7]; // 56 bits
    // PC1 permutation
    for (int i = 0; i < 56; i++) {
        int bit = PC1[i] - 1;
        int byte_idx = bit / 8;
        int bit_idx = 7 - (bit % 8);
        int out_byte = i / 8;
        int out_bit = 7 - (i % 8);
        if (key[byte_idx] & (1 << bit_idx))
            perm_key[out_byte] |= (1 << out_bit);
        else
            perm_key[out_byte] &= ~(1 << out_bit);
    }

    uint8_t C[4] = {0}, D[4] = {0}; // 28 bits each
    for (int i = 0; i < 28; i++) {
        int bit = i;
        int byte_idx = bit / 8;
        int bit_idx = 7 - (bit % 8);
        if (perm_key[byte_idx] & (1 << bit_idx))
            C[byte_idx] |= (1 << bit_idx);
        else
            C[byte_idx] &= ~(1 << bit_idx);
    }
    for (int i = 28; i < 56; i++) {
        int bit = i;
        int byte_idx = bit / 8;
        int bit_idx = 7 - (bit % 8);
        if (perm_key[byte_idx] & (1 << bit_idx))
            D[byte_idx-3] |= (1 << bit_idx);
        else
            D[byte_idx-3] &= ~(1 << bit_idx);
    }

    for (int round = 0; round < 16; round++) {
        // Rotate C and D
        for (int s = 0; s < shifts[round]; s++) {
            uint8_t c0 = (C[0] >> 7) & 1;
            uint8_t d0 = (D[0] >> 7) & 1;
            for (int i = 0; i < 3; i++) {
                C[i] = (C[i] << 1) | ((C[i+1] >> 7) & 1);
                D[i] = (D[i] << 1) | ((D[i+1] >> 7) & 1);
            }
            C[3] = (C[3] << 1) | c0;
            D[3] = (D[3] << 1) | d0;
            // Mask to 28 bits
            C[3] &= 0xF0;
            D[3] &= 0xF0;
        }

        // Combine C and D into 56-bit block
        uint8_t cd[7];
        for (int i = 0; i < 4; i++) cd[i] = C[i];
        for (int i = 4; i < 7; i++) cd[i] = D[i-4];

        // PC2 permutation to get round key
        for (int i = 0; i < 48; i++) {
            int bit = PC2[i] - 1;
            int byte_idx = bit / 8;
            int bit_idx = 7 - (bit % 8);
            int out_byte = i / 8;
            int out_bit = 7 - (i % 8);
            if (cd[byte_idx] & (1 << bit_idx))
                round_keys[round][out_byte] |= (1 << out_bit);
            else
                round_keys[round][out_byte] &= ~(1 << out_bit);
        }
    }
}

// DES round function f(R, K) -> 32-bit output
static uint32_t f(uint32_t R, const uint8_t K[6]) {
    // Expansion E
    uint64_t expanded = 0;
    for (int i = 0; i < 48; i++) {
        int bit = E[i] - 1;
        if (R & (1 << (31 - bit)))
            expanded |= (1ULL << (47 - i));
    }

    // XOR with round key
    uint64_t xored = expanded;
    for (int i = 0; i < 6; i++) {
        uint8_t kb = K[i];
        for (int j = 0; j < 8; j++) {
            int bit_idx = 47 - (i*8 + j);
            if (kb & (1 << (7 - j))) {
                xored ^= (1ULL << bit_idx);
            }
        }
    }

    // S-box substitution
    uint32_t output = 0;
    for (int i = 0; i < 8; i++) {
        int start = 47 - (i*6);
        uint8_t bits = (xored >> (start - 5)) & 0x3F;
        uint8_t row = ((bits >> 5) & 1) | ((bits & 1) << 1);
        uint8_t col = (bits >> 1) & 0x0F;
        uint8_t val = sbox[i][row][col];
        output |= (val << (28 - i*4));
    }

    // Permutation P
    uint32_t permuted = 0;
    for (int i = 0; i < 32; i++) {
        int bit = P[i] - 1;
        if (output & (1 << (31 - bit)))
            permuted |= (1 << (31 - i));
    }
    return permuted;
}

// DES encrypt one 64-bit block
static void des_encrypt_block(const uint8_t *plain, const uint8_t round_keys[16][6], uint8_t *cipher) {
    // Initial permutation
    uint64_t block = 0;
    for (int i = 0; i < 64; i++) {
        int bit = IP[i] - 1;
        int byte_idx = bit / 8;
        int bit_idx = 7 - (bit % 8);
        if (plain[byte_idx] & (1 << bit_idx))
            block |= (1ULL << (63 - i));
    }

    uint32_t L = (block >> 32) & 0xFFFFFFFF;
    uint32_t R = block & 0xFFFFFFFF;

    for (int round = 0; round < 16; round++) {
        uint32_t newL = R;
        uint32_t newR = L ^ f(R, round_keys[round]);
        L = newL;
        R = newR;
    }

    // Final swap
    uint64_t preoutput = ((uint64_t)R << 32) | L;

    // Inverse IP
    for (int i = 0; i < 64; i++) {
        int bit = IPinv[i] - 1;
        int byte_idx = i / 8;
        int bit_idx = 7 - (i % 8);
        if (preoutput & (1ULL << (63 - bit)))
            cipher[byte_idx] |= (1 << bit_idx);
        else
            cipher[byte_idx] &= ~(1 << bit_idx);
    }
}

int main() {
    char *plaintext_hex = getenv("TEST_PLAINTEXT");
    char *key_hex = getenv("TEST_KEY");
    char *iv_hex = getenv("TEST_IV");

    if (!plaintext_hex || !key_hex || !iv_hex) {
        fprintf(stderr, "Missing environment variables\n");
        return 1;
    }

    size_t pt_len = strlen(plaintext_hex) / 2;
    size_t key_len = 8; // DES key is 8 bytes
    size_t iv_len = 8;

    uint8_t *plaintext = malloc(pt_len);
    uint8_t key[8];
    uint8_t iv[8];

    hex2bytes(plaintext_hex, plaintext, pt_len);
    hex2bytes(key_hex, key, key_len);
    hex2bytes(iv_hex, iv, iv_len);

    // Generate round keys
    uint8_t round_keys[16][6];
    key_schedule(key, round_keys);

    // CFB mode encryption
    uint8_t *ciphertext = malloc(pt_len);
    uint8_t feedback[8];