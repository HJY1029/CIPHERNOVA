1. Define DES constants (IP, FP, S-boxes, PC1, PC2, shift schedule, E, P).
2. Implement helper functions:
   - hex_to_bytes: convert hex string to byte array
   - bytes_to_hex: convert byte array to hex string
   - permute: apply permutation table to bit array
   - left_shift: circular left shift
   - generate_subkeys: from 64-bit key (with parity bits) produce 16 48-bit subkeys
   - feistel_function: takes 32-bit half and 48-bit subkey, returns 32-bit
   - des_encrypt_block: encrypt a single 64-bit block
3. Implement OFB mode:
   - Read env vars
   - Convert hex to bytes
   - Generate subkeys from key
   - Initialize feedback = IV
   - For each plaintext block:
       - feedback = des_encrypt_block(feedback)
       - ciphertext_block = plaintext_block XOR feedback (first len(plaintext) bytes)
   - Output hex string
4. Main function: call OFB encrypt, print result