#!/usr/bin/env python3
import os
from Crypto.Cipher import DES

def main():
    # Read environment variables (hex strings)
    plaintext_hex = os.environ.get('TEST_PLAINTEXT', '')
    key_hex = os.environ.get('TEST_KEY', '')
    iv_hex = os.environ.get('TEST_IV', '')

    # Convert hex to bytes
    plaintext = bytes.fromhex(plaintext_hex)
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)

    # Create DES cipher in CFB mode (segment size = 8 bits)
    cipher = DES.new(key, DES.MODE_CFB, iv, segment_size=8)

    # Encrypt
    ciphertext = cipher.encrypt(plaintext)

    # Output as lowercase hex
    print(ciphertext.hex())

if __name__ == '__main__':
    main()