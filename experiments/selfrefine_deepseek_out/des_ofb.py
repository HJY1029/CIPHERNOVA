#!/usr/bin/env python3
import os
from Crypto.Cipher import DES

def main():
    try:
        # Read environment variables
        plaintext_hex = os.environ.get('PLAINTEXT', '')
        key_hex = os.environ.get('KEY', '')
        iv_hex = os.environ.get('IV', '')

        # Validate inputs
        if not all([plaintext_hex, key_hex, iv_hex]):
            print("Error: Missing required environment variables", file=sys.stderr)
            sys.exit(1)

        # Convert hex strings to bytes
        plaintext = bytes.fromhex(plaintext_hex)
        key = bytes.fromhex(key_hex)
        iv = bytes.fromhex(iv_hex)

        # Validate DES key size (must be 8 bytes)
        if len(key) != 8:
            print("Error: DES key must be exactly 8 bytes", file=sys.stderr)
            sys.exit(1)

        # Validate IV size (must be 8 bytes for DES)
        if len(iv) != 8:
            print("Error: IV must be exactly 8 bytes", file=sys.stderr)
            sys.exit(1)

        # Create DES cipher in OFB mode (no padding needed for stream mode)
        cipher = DES.new(key, DES.MODE_OFB, iv=iv)

        # Encrypt directly without padding
        ciphertext = cipher.encrypt(plaintext)

        # Print ciphertext as lowercase hex without trailing newline
        print(ciphertext.hex(), end='')

    except ValueError as e:
        print(f"Error: Invalid hex string - {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    import sys
    main()