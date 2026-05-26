function des_encrypt_block(block, key):
    // standard DES block encryption
    return ciphertext_block

function cfb_encrypt(plaintext, key, iv):
    ciphertext = []
    feedback = iv
    for each block in plaintext (64-bit blocks):
        encrypted_feedback = des_encrypt_block(feedback, key)
        cipher_block = xor(encrypted_feedback, block)
        ciphertext.append(cipher_block)
        feedback = cipher_block  # for CFB-64
    return concatenated ciphertext

main:
    plaintext_hex = os.environ['TEST_PLAINTEXT']
    key_hex = os.environ['TEST_KEY']
    iv_hex = os.environ['TEST_IV']
    plaintext = bytes.fromhex(plaintext_hex)
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    ciphertext = cfb_encrypt(plaintext, key, iv)
    print(ciphertext.hex())