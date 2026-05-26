import os
import struct

# DES constants
IP = [58, 50, 42, 34, 26, 18, 10, 2,
      60, 52, 44, 36, 28, 20, 12, 4,
      62, 54, 46, 38, 30, 22, 14, 6,
      64, 56, 48, 40, 32, 24, 16, 8,
      57, 49, 41, 33, 25, 17, 9, 1,
      59, 51, 43, 35, 27, 19, 11, 3,
      61, 53, 45, 37, 29, 21, 13, 5,
      63, 55, 47, 39, 31, 23, 15, 7]

IP_INV = [40, 8, 48, 16, 56, 24, 64, 32,
          39, 7, 47, 15, 55, 23, 63, 31,
          38, 6, 46, 14, 54, 22, 62, 30,
          37, 5, 45, 13, 53, 21, 61, 29,
          36, 4, 44, 12, 52, 20, 60, 28,
          35, 3, 43, 11, 51, 19, 59, 27,
          34, 2, 42, 10, 50, 18, 58, 26,
          33, 1, 41, 9, 49, 17, 57, 25]

E = [32, 1, 2, 3, 4, 5,
     4, 5, 6, 7, 8, 9,
     8, 9, 10, 11, 12, 13,
     12, 13, 14, 15, 16, 17,
     16, 17, 18, 19, 20, 21,
     20, 21, 22, 23, 24, 25,
     24, 25, 26, 27, 28, 29,
     28, 29, 30, 31, 32, 1]

P = [16, 7, 20, 21, 29, 12, 28, 17,
     1, 15, 23, 26, 5, 18, 31, 10,
     2, 8, 24, 14, 32, 27, 3, 9,
     19, 13, 30, 6, 22, 11, 4, 25]

PC1 = [57, 49, 41, 33, 25, 17, 9,
       1, 58, 50, 42, 34, 26, 18,
       10, 2, 59, 51, 43, 35, 27,
       19, 11, 3, 60, 52, 44, 36,
       63, 55, 47, 39, 31, 23, 15,
       7, 62, 54, 46, 38, 30, 22,
       14, 6, 61, 53, 45, 37, 29,
       21, 13, 5, 28, 20, 12, 4]

PC2 = [14, 17, 11, 24, 1, 5,
       3, 28, 15, 6, 21, 10,
       23, 19, 12, 4, 26, 8,
       16, 7, 27, 20, 13, 2,
       41, 52, 31, 37, 47, 55,
       30, 40, 51, 45, 33, 48,
       44, 49, 39, 56, 34, 53,
       46, 42, 50, 36, 29, 32]

SHIFT_SCHEDULE = [1, 1, 2, 2, 2, 2, 2, 2,
                  1, 2, 2, 2, 2, 2, 2, 1]

S_BOXES = [
    # S1
    [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
     [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
     [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
     [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]],
    # S2
    [[15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10],
     [3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5],
     [0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15],
     [13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9]],
    # S3
    [[10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8],
     [13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1],
     [13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7],
     [1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12]],
    # S4
    [[7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15],
     [13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9],
     [10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4],
     [3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14]],
    # S5
    [[2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9],
     [14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6],
     [4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14],
     [11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3]],
    # S6
    [[12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11],
     [10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8],
     [9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6],
     [4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13]],
    # S7
    [[4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1],
     [13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6],
     [1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2],
     [6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12]],
    # S8
    [[13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7],
     [1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2],
     [7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8],
     [2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]]
]

def permute(block, table):
    """Permute a 64-bit integer according to table (1-indexed)."""
    result = 0
    for i, pos in enumerate(table):
        bit = (block >> (64 - pos)) & 1
        result |= (bit << (63 - i))
    return result

def permute_48(block, table):
    """Permute a 48-bit integer (for expansion/PC2)."""
    result = 0
    for i, pos in enumerate(table):
        bit = (block >> (48 - pos)) & 1
        result |= (bit << (47 - i))
    return result

def permute_32(block, table):
    """Permute a 32-bit integer (for P-box)."""
    result = 0
    for i, pos in enumerate(table):
        bit = (block >> (32 - pos)) & 1
        result |= (bit << (31 - i))
    return result

def s_box_substitution(expanded_half):
    """Apply S-box substitution on 48-bit input, return 32-bit output."""
    result = 0
    for i in range(8):
        chunk = (expanded_half >> (42 - 6*i)) & 0x3F
        row = ((chunk >> 4) & 0x2) | (chunk & 0x1)
        col = (chunk >> 1) & 0xF
        val = S_BOXES[i][row][col]
        result = (result << 4) | val
    return result

def feistel(right, subkey):
    """DES Feistel function: expand, XOR with subkey, S-box, P-box."""
    expanded = permute_48(right, E)
    xored = expanded ^ subkey
    substituted = s_box_substitution(xored)
    return permute_32(substituted, P)

def key_schedule(key):
    """Generate 16 round subkeys from 64-bit key."""
    # Drop parity bits (PC1)
    key56 = 0
    for i, pos in enumerate(PC1):
        bit = (key >> (64 - pos)) & 1
        key56 |= (bit << (55 - i))
    
    # Split into C and D (28 bits each)
    C = (key56 >> 28) & 0xFFFFFFF
    D = key56 & 0xFFFFFFF
    
    subkeys = []
    for shift in SHIFT_SCHEDULE:
        C = ((C << shift) | (C >> (28 - shift))) & 0xFFFFFFF
        D = ((D << shift) | (D >> (28 - shift))) & 0xFFFFFFF
        combined = (C << 28) | D
        subkey = 0
        for i, pos in enumerate(PC2):
            bit = (combined >> (56 - pos)) & 1
            subkey |= (bit << (47 - i))
        subkeys.append(subkey)
    return subkeys

def des_encrypt_block(block, subkeys):
    """Encrypt a single 64-bit block with DES."""
    block = permute(block, IP)
    left = (block >> 32) & 0xFFFFFFFF
    right = block & 0xFFFFFFFF
    
    for i in range(16):
        new_left = right
        new_right = left ^ feistel(right, subkeys[i])
        left, right = new_left, new_right
    
    combined = (right << 32) | left
    return permute(combined, IP_INV)

def pkcs7_pad(data):
    pad_len = 8 - (len(data) % 8)
    return data + bytes([pad_len] * pad_len)

def hex_to_bytes(hex_str):
    return bytes.fromhex(hex_str)

def bytes_to_hex(b):
    return b.hex()

def des_cbc_encrypt(plaintext, key, iv):
    subkeys = key_schedule(key)
    padded = pkcs7_pad(plaintext)
    ciphertext = b""
    prev = iv
    for i in range(0, len(padded), 8):
        block = struct.unpack(">Q", padded[i:i+8])[0]
        prev_int = struct.unpack(">Q", prev)[0]
        xored = block ^ prev_int
        encrypted = des_encrypt_block(xored, subkeys)
        ct_block = struct.pack(">Q", encrypted)
        ciphertext += ct_block
        prev = ct_block
    return ciphertext

def main():
    plaintext_hex = os.environ.get("TEST_PLAINTEXT", "")
    key_hex = os.environ.get("TEST_KEY", "")
    iv_hex = os.environ.get("TEST_IV", "")
    
    plaintext = hex_to_bytes(plaintext_hex)
    key = struct.unpack(">Q", hex_to_bytes(key_hex))[0]
    iv = hex_to_bytes(iv_hex)
    
    ciphertext = des_cbc_encrypt(plaintext, key, iv)
    print(bytes_to_hex(ciphertext))

if __name__ == "__main__":
    main()