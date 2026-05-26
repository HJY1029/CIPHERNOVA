#!/usr/bin/env python3
import os
from Crypto.Cipher import DES

def main():
    # Read hex-encoded plaintext and key from environment variables
    plaintext_hex = os.environ.get('TEST_PLAINTEXT', '')
    key_hex = os.environ.get('TEST_KEY', '')
    
    # Convert hex strings to bytes
    plaintext = bytes.fromhex(plaintext_hex)
    key = bytes.fromhex(key_hex)
    
    # Create DES cipher in ECB mode
    cipher = DES.new(key, DES.MODE_ECB)
    
    # Encrypt the plaintext
    ciphertext = cipher.encrypt(plaintext)
    
    # Output ciphertext as lowercase hex
    print(ciphertext.hex().lower())

if __name__ == '__main__':
    main()