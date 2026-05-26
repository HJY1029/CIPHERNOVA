"""Task: DES-CFB encrypt in Python. Read plaintext/key and IV from environment variables TEST_PLAINTEXT, TEST_KEY, TEST_IV (hex strings). Print ciphertext as lowercase hex. IV from TEST_IV (hex). Output only complete runnable code."""

    def __init__(self, ciphertext, key, iv):
        self.ciphertext = ciphertext
        self.key = key
        self.iv = iv

    def encrypt(self):
        """Task: DES-CFB encrypt in Python. Print ciphertext as lowercase hex. IV from TEST_IV (hex). Output only complete runnable code."""
        return self.ciphertext.decode('utf-8').lower()

    def decrypt(self):
        """Task: DES-CFB decrypt in Python. Print ciphertext as lowercase hex. IV from TEST_IV (hex). Output only complete runnable code."""
        return self.ciphertext.encode('utf-8').lower()

    def __repr__(self):
        return '<DES-CFB encrypt in Python>'
