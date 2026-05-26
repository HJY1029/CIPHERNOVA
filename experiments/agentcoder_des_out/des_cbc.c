#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

// DES Permutation tables
static const int IP[] = {
    58,50,42,34,26,18,10,2,
    60,52,44,36,28,20,12,4,
    62,54,46,38,30,22,14,6,
    64,56,48,40,32,24,16,8,
    57,49,41,33,25,17,9,1,
    59,51,43,35,27,19,11,3,
    61,53,45,37,29,21,13,5,
    63,55,47,39,31,23,15,7
};

static const int FP[] = {
    40,8,48,16,56,24,64,32,
    39,7,47,15,55,23,63,31,
    38,6,46,14,54,22,62,30,
    37,5,45,13,53,21,61,29,
    36,4,44,12,52,20,60,28,
    35,3,43,11,51,19,59,27,
    34,2,42,10,50,18,58,26,
    33,1,41,9,49,17,57,25
};

static const int E[] = {
    32,1,2,3,4,5,
    4,5,6,7,8,9,
    8,9,10,11,12,13,
    12,13,14,15,16,17,
    16,17,18,19,20,21,
    20,21,22,23,24,25,
    24,25,26,27,28,29,
    28,29,30,31,32,1
};

static const int P[] = {
    16,7,20,21,29,12,28,17,
    1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9,
    19,13,30,6,22,11,4,25
};

static const int PC1[] = {
    57,49,41,33,25,17,9,
    1,58,50,42,34,26,18,
    10,2,59,51,43,35,27,
    19,11,3,60,52,44,36,
    63,55,47,39,31,23,15,
    7,62,54,46,38,30,22,
    14,6,61,53,45,37,29,
    21,13,5,28,20,12,4
};

static const int PC2[] = {
    14,17,11,24,1,5,
    3,28,15,6,21,10,
    23,19,12,4,26,8,
    16,7,27,20,13,2,
    41,52,31,37,47,55,
    30,40,51,45,33,48,
    44,49,39,56,34,53,
    46,42,50,36,29,32
};

static const int SHIFT[] = {1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1};

static const uint8_t S[8][4][16] = {
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

static uint64_t permute(uint64_t input, const int *table, int n) {
    uint64_t result = 0;
    for (int i = 0; i < n; i++) {
        result <<= 1;
        result |= (input >> (64 - table[i])) & 1;
    }
    return result;
}

static uint64_t des_encrypt_block(uint64_t plaintext, uint64_t key) {
    uint64_t permuted = permute(plaintext, IP, 64);
    uint32_t left = permuted >> 32;
    uint32_t right = permuted & 0xFFFFFFFF;

    // Generate subkeys
    uint64_t perm_key = permute(key, PC1, 56);
    uint32_t C = perm_key >> 28;
    uint32_t D = perm_key & 0x0FFFFFFF;
    uint64_t subkeys[16];

    for (int round = 0; round < 16; round++) {
        C = ((C << SHIFT[round]) | (C >> (28 - SHIFT[round]))) & 0x0FFFFFFF;
        D = ((D << SHIFT[round]) | (D >> (28 - SHIFT[round]))) & 0x0FFFFFFF;
        uint64_t CD = ((uint64_t)C << 28) | D;
        subkeys[round] = permute(CD, PC2, 48);
    }

    for (int round = 0; round < 16; round++) {
        uint64_t expanded = 0;
        for (int i = 0; i < 48; i++) {
            expanded <<= 1;
            expanded |= (right >> (32 - E[i])) & 1;
        }
        uint64_t xored = expanded ^ subkeys[round];
        uint32_t sbox_out = 0;
        for (int i = 0; i < 8; i++) {
            int row = ((xored >> (47 - 6*i)) & 0x20) | ((xored >> (42 - 6*i)) & 1);
            int col = (xored >> (43 - 6*i)) & 0x0F;
            sbox_out = (sbox_out << 4) | S[i][row][col];
        }
        uint32_t permuted_sbox = 0;
        for (int i = 0; i < 32; i++) {
            permuted_sbox <<= 1;
            permuted_sbox |= (sbox_out >> (32 - P[i])) & 1;
        }
        uint32_t new_right = left ^ permuted_sbox;
        left = right;
        right = new_right;
    }

    uint64_t combined = ((uint64_t)right << 32) | left;
    return permute(combined, FP, 64);
}

static void hex_to_bytes(const char *hex, uint8_t *bytes, size_t *len) {
    size_t hex_len = strlen(hex);
    *len = hex_len / 2;
    for (size_t i = 0; i < *len; i++) {
        sscanf(hex + 2*i, "%2hhx", &bytes[i]);
    }
}

static void bytes_to_hex(const uint8_t *bytes, size_t len, char *hex) {
    for (size_t i = 0; i < len; i++) {
        sprintf(hex + 2*i, "%02x", bytes[i]);
    }
    hex[2*len] = '\0';
}

int main() {
    const char *plaintext_hex = getenv("TEST_PLAINTEXT");
    const char *key_hex = getenv("TEST_KEY");
    const char *iv_hex = getenv("TEST_IV");

    if (!plaintext_hex || !key_hex || !iv_hex) {
        fprintf(stderr, "Missing environment variables\n");
        return 1;
    }

    uint8_t key[8], iv[8];
    size_t key_len, iv_len;
    hex_to_bytes(key_hex, key, &key_len);
    hex_to_bytes(iv_hex, iv, &iv_len);

    uint8_t plaintext[1024];
    size_t pt_len;
    hex_to_bytes(plaintext_hex, plaintext, &pt_len);

    // PKCS#7 padding
    size_t padded_len = pt_len + (8 - pt_len % 8);
    uint8_t padded[1024];
    memcpy(padded, plaintext, pt_len);
    uint8_t pad_val = 8 - pt_len % 8;
    for (size_t i = pt_len; i < padded_len; i++) {
        padded[i] = pad_val;
    }

    uint64_t key64 = 0;
    for (int i = 0; i < 8; i++) {
        key64 = (key64 << 8) | key[i];
    }

    uint64_t iv64 = 0;
    for (int i = 0; i < 8; i++) {
        iv64 = (iv64 << 8) | iv[i];
    }

    uint8_t ciphertext[1024];
    uint64_t prev = iv64;

    for (size_t i = 0; i < padded_len; i += 8) {
        uint64_t block = 0;
        for (int j = 0; j < 8; j++) {
            block = (block << 8) | padded[i + j];
        }
        block ^= prev;
        uint64_t enc = des_encrypt_block(block, key64);
        prev = enc;
        for (int j = 0; j < 8; j++) {
            ciphertext[i + j] = (enc >> (56 - 8*j)) & 0xFF;
        }
    }

    char hex_output[2048];
    bytes_to_hex(ciphertext, padded_len, hex_output);
    printf("%s\n", hex_output);

    return 0;
}