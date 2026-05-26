#!/usr/bin/env python3
import os
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    # Read environment variables
    plaintext_hex = os.environ.get('TEST_PLAINTEXT', '')
    key_hex = os.environ.get('TEST_KEY', '')
    iv_hex = os.environ.get('TEST_IV', '')

    # Convert hex strings to bytes
    plaintext = bytes.fromhex(plaintext_hex)
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)

    # Create DES cipher in OFB mode
    cipher = DES.new(key, DES.MODE_OFB, iv=iv)

    # Encrypt the plaintext (no padding needed for OFB mode)
    ciphertext = cipher.encrypt(plaintext)

    # Print ciphertext as lowercase hex
    print(ciphertext.hex())

if __name__ == '__main__':
    main()