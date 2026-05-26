"""Task: DES-CBC encrypt in Python. Read plaintext/key and IV from environment variables TEST_PLAINTEXT, TEST_KEY, TEST_IV (hex strings). Print ciphertext as lowercase hex. IV from TEST_IV (hex). Output only complete runnable code."""

    def __init__(self, key, iv):
        self.key = key
        self.iv = iv
        self.ciphertext = ""

    def encrypt(self, plaintext):
        """Encrypt plaintext using the DES-CBC cipher.

        :param plaintext: plaintext to encrypt
        :return: ciphertext
        """
        self.ciphertext = self.encrypt_ciphertext(plaintext)

    def encrypt_ciphertext(self, plaintext):
        """Encrypt ciphertext using the DES-CBC cipher.

        :param plaintext: plaintext to encrypt
        :return: ciphertext
        """
        return self.ciphertext.upper() + self.iv + plaintext

    def decrypt(self, ciphertext):
        """Decrypt ciphertext using the DES-CBC cipher.

        :param ciphertext: ciphertext to decrypt
        :return: plaintext
        """
        return ciphertext.upper() + self.iv + self.ciphertext

    def decrypt_ciphertext(self, ciphertext):
        """Decrypt ciphertext using the DES-CBC cipher.

        :param ciphertext: ciphertext to decrypt
        :return: plaintext
        """
        return ciphertext.upper() + self.iv + self.ciphertext
