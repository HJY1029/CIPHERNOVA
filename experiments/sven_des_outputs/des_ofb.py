"""Task: DES-OFB encrypt in Python. Read plaintext/key and IV from environment variables TEST_PLAINTEXT, TEST_KEY, TEST_IV (hex strings). Print ciphertext as lowercase hex. IV from TEST_IV (hex). Output only complete runnable code."""

    def __init__(self):
        super(DESOFBEncrypt, self).__init__()
        self.__plaintext = ""
        self.__key = ""
        self.__iv = ""
        self.__ciphertext = ""
        self.__ciphertext_lower = ""
        self.__ciphertext_hex = ""
        self.__ciphertext_hex_lower = ""
        self.__ciphertext_hex_hex = ""

    def run(self):
        """Task: DES-OFB encrypt in Python. Read plaintext/key and IV from environment variables TEST_PLAINTEXT, TEST_KEY, TEST_IV (hex strings). Print ciphertext as lowercase hex. IV from TEST_IV (hex). Output only complete runnable code."""
        self.__plaintext = os.environ['TEST_PLAINTEXT']
        self.__key = os.environ['TEST_KEY']
        self.__iv = os.environ['TEST_IV']
        self.__ciphertext = DES.DES_OFB_encrypt(self.__plaintext, self.__key, self.__iv)

    def get_ciphertext(self):
        """Task: DES-OFB encrypt in Python. Read ciphertext as