"""Task: DES-ECB encrypt in Python. Read plaintext/key from environment variables TEST_PLAINTEXT, TEST_KEY (hex strings). Print ciphertext as lowercase hex. (IV not used for ECB). Output only complete runnable code."""

    def __init__(self):
        """Initialize the class."""
        self.plaintext = os.environ['TEST_PLAINTEXT']
        self.key = os.environ['TEST_KEY']

    def run(self):
        """Run the class."""
        ciphertext = encrypt(self.plaintext, self.key)
        print('ciphertext: {0}'.format(ciphertext))
