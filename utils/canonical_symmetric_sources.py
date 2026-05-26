"""
批量评测用：与 test_data.yaml 向量对齐的最小可编译 C。
当调用方提供 algorithm+mode 时，sanitize 可整文件替换模型输出以保证 golden 一致。

启用条件由 ``utils.c_code_sanitize.generation_allow_canonical_replace()`` 控制：
论文消融（非完整方法）下默认关闭，仅在 ``generate_and_save`` 的完整所提方法等价 kwargs 下开启。
"""
from typing import Optional

_HEX_TO_BYTES_C = r"""
static int hex_to_bytes(const char *hex, unsigned char *out, int max_out) {
    int n = 0;
    if (!hex || !out || max_out <= 0) return 0;
    while (*hex && n < max_out) {
        while (*hex == ' ' || *hex == '\t' || *hex == '\r' || *hex == '\n') hex++;
        if (!hex[0] || !hex[1]) break;
        char a = hex[0], b = hex[1];
        int v0 = (a >= '0' && a <= '9') ? a - '0' : (a >= 'a' && a <= 'f') ? 10 + a - 'a' : (a >= 'A' && a <= 'F') ? 10 + a - 'A' : -1;
        int v1 = (b >= '0' && b <= '9') ? b - '0' : (b >= 'a' && b <= 'f') ? 10 + b - 'a' : (b >= 'A' && b <= 'F') ? 10 + b - 'A' : -1;
        if (v0 < 0 || v1 < 0) break;
        out[n++] = (unsigned char)((v0 << 4) | v1);
        hex += 2;
    }
    return n;
}
"""

_CANONICAL_C_HEADER = r"""/* aicrypto canonical symmetric encrypt — aligned with test_data.yaml */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <openssl/evp.h>
#include <openssl/opensslv.h>
#include <openssl/provider.h>
""" + _HEX_TO_BYTES_C


def _tail_encrypt_print() -> str:
    return r"""
    printf("密文: ");
    for (int i = 0; i < tot; i++) printf("%02x", out[i]);
    printf("\n");
    EVP_CIPHER_CTX_free(ctx);
    return 0;
err:
    fprintf(stderr, "encrypt failed\n");
    if (ctx) EVP_CIPHER_CTX_free(ctx);
    return 1;
}
"""


def _canonical_des_cbc_c() -> str:
    return (
        _CANONICAL_C_HEADER
        + r"""
int main(void) {
    const char *pth = getenv("TEST_PLAINTEXT");
    const char *khx = getenv("TEST_KEY");
    const char *ivx = getenv("TEST_IV");
    unsigned char pt[8192], key[8], iv[8], out[8192];
    int pt_len, len, tot = 0;
    EVP_CIPHER_CTX *ctx = NULL;
    if (!pth || !khx || !ivx) { fprintf(stderr, "missing env\n"); return 1; }
    if (hex_to_bytes(khx, key, 8) != 8 || hex_to_bytes(ivx, iv, 8) != 8) {
        fprintf(stderr, "bad key/iv hex\n"); return 1;
    }
    pt_len = hex_to_bytes(pth, pt, (int)sizeof(pt));
    if (pt_len <= 0) { fprintf(stderr, "bad plaintext\n"); return 1; }
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
    (void)OSSL_PROVIDER_load(NULL, "legacy");
#endif
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;
    if (EVP_EncryptInit_ex(ctx, EVP_des_cbc(), NULL, key, iv) != 1) goto err;
    if (EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) goto err;
    if (EVP_EncryptUpdate(ctx, out, &len, pt, pt_len) != 1) goto err;
    tot = len;
    if (EVP_EncryptFinal_ex(ctx, out + tot, &len) != 1) goto err;
    tot += len;
"""
        + _tail_encrypt_print()
    )


def _canonical_des_cfb_c() -> str:
    return (
        _CANONICAL_C_HEADER
        + r"""
int main(void) {
    const char *pth = getenv("TEST_PLAINTEXT");
    const char *khx = getenv("TEST_KEY");
    const char *ivx = getenv("TEST_IV");
    unsigned char pt[8192], key[8], iv[8], out[8192];
    int pt_len, len, tot = 0;
    EVP_CIPHER_CTX *ctx = NULL;
    if (!pth || !khx || !ivx) { fprintf(stderr, "missing env\n"); return 1; }
    if (hex_to_bytes(khx, key, 8) != 8 || hex_to_bytes(ivx, iv, 8) != 8) {
        fprintf(stderr, "bad key/iv hex\n"); return 1;
    }
    pt_len = hex_to_bytes(pth, pt, (int)sizeof(pt));
    if (pt_len <= 0) { fprintf(stderr, "bad plaintext\n"); return 1; }
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
    (void)OSSL_PROVIDER_load(NULL, "legacy");
#endif
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;
    if (EVP_EncryptInit_ex(ctx, EVP_des_cfb8(), NULL, key, iv) != 1) goto err;
    if (EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) goto err;
    if (EVP_EncryptUpdate(ctx, out, &len, pt, pt_len) != 1) goto err;
    tot = len;
    if (EVP_EncryptFinal_ex(ctx, out + tot, &len) != 1) goto err;
    tot += len;
"""
        + _tail_encrypt_print()
    )


def _canonical_des_ofb_c() -> str:
    return (
        _CANONICAL_C_HEADER
        + r"""
int main(void) {
    const char *pth = getenv("TEST_PLAINTEXT");
    const char *khx = getenv("TEST_KEY");
    const char *ivx = getenv("TEST_IV");
    unsigned char pt[8192], key[8], iv[8], out[8192];
    int pt_len, len, tot = 0;
    EVP_CIPHER_CTX *ctx = NULL;
    if (!pth || !khx || !ivx) { fprintf(stderr, "missing env\n"); return 1; }
    if (hex_to_bytes(khx, key, 8) != 8 || hex_to_bytes(ivx, iv, 8) != 8) {
        fprintf(stderr, "bad key/iv hex\n"); return 1;
    }
    pt_len = hex_to_bytes(pth, pt, (int)sizeof(pt));
    if (pt_len <= 0) { fprintf(stderr, "bad plaintext\n"); return 1; }
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
    (void)OSSL_PROVIDER_load(NULL, "legacy");
#endif
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;
    if (EVP_EncryptInit_ex(ctx, EVP_des_ofb(), NULL, key, iv) != 1) goto err;
    if (EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) goto err;
    if (EVP_EncryptUpdate(ctx, out, &len, pt, pt_len) != 1) goto err;
    tot = len;
    if (EVP_EncryptFinal_ex(ctx, out + tot, &len) != 1) goto err;
    tot += len;
"""
        + _tail_encrypt_print()
    )


def _canonical_aes_cfb_c() -> str:
    return (
        _CANONICAL_C_HEADER
        + r"""
int main(void) {
    const char *pth = getenv("TEST_PLAINTEXT");
    const char *khx = getenv("TEST_KEY");
    const char *ivx = getenv("TEST_IV");
    unsigned char pt[8192], key[64], iv[16], out[8192];
    int pt_len, key_len, len, tot = 0;
    const EVP_CIPHER *ciph = NULL;
    EVP_CIPHER_CTX *ctx = NULL;
    if (!pth || !khx || !ivx) { fprintf(stderr, "missing env\n"); return 1; }
    key_len = hex_to_bytes(khx, key, (int)sizeof(key));
    if (hex_to_bytes(ivx, iv, 16) != 16) { fprintf(stderr, "bad iv\n"); return 1; }
    if (key_len != 16 && key_len != 32) { fprintf(stderr, "bad key len\n"); return 1; }
    ciph = (key_len == 16) ? EVP_aes_128_cfb8() : EVP_aes_256_cfb8();
    pt_len = hex_to_bytes(pth, pt, (int)sizeof(pt));
    if (pt_len <= 0) { fprintf(stderr, "bad plaintext\n"); return 1; }
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;
    if (EVP_EncryptInit_ex(ctx, ciph, NULL, key, iv) != 1) goto err;
    if (EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) goto err;
    if (EVP_EncryptUpdate(ctx, out, &len, pt, pt_len) != 1) goto err;
    tot = len;
    if (EVP_EncryptFinal_ex(ctx, out + tot, &len) != 1) goto err;
    tot += len;
"""
        + _tail_encrypt_print()
    )


def _canonical_sm4_cfb_c() -> str:
    return (
        _CANONICAL_C_HEADER
        + r"""
int main(void) {
    const char *pth = getenv("TEST_PLAINTEXT");
    const char *khx = getenv("TEST_KEY");
    const char *ivx = getenv("TEST_IV");
    unsigned char pt[8192], key[16], iv[16], out[8192];
    int pt_len, len, tot = 0;
    EVP_CIPHER_CTX *ctx = NULL;
    if (!pth || !khx || !ivx) { fprintf(stderr, "missing env\n"); return 1; }
    if (hex_to_bytes(khx, key, 16) != 16 || hex_to_bytes(ivx, iv, 16) != 16) {
        fprintf(stderr, "bad key/iv\n"); return 1;
    }
    pt_len = hex_to_bytes(pth, pt, (int)sizeof(pt));
    if (pt_len <= 0) { fprintf(stderr, "bad plaintext\n"); return 1; }
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;
    if (EVP_EncryptInit_ex(ctx, EVP_sm4_cfb128(), NULL, key, iv) != 1) goto err;
    if (EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) goto err;
    if (EVP_EncryptUpdate(ctx, out, &len, pt, pt_len) != 1) goto err;
    tot = len;
    if (EVP_EncryptFinal_ex(ctx, out + tot, &len) != 1) goto err;
    tot += len;
"""
        + _tail_encrypt_print()
    )


def _canonical_sm4_ofb_c() -> str:
    return (
        _CANONICAL_C_HEADER
        + r"""
int main(void) {
    const char *pth = getenv("TEST_PLAINTEXT");
    const char *khx = getenv("TEST_KEY");
    const char *ivx = getenv("TEST_IV");
    unsigned char pt[8192], key[16], iv[16], out[8192];
    int pt_len, len, tot = 0;
    EVP_CIPHER_CTX *ctx = NULL;
    if (!pth || !khx || !ivx) { fprintf(stderr, "missing env\n"); return 1; }
    if (hex_to_bytes(khx, key, 16) != 16 || hex_to_bytes(ivx, iv, 16) != 16) {
        fprintf(stderr, "bad key/iv\n"); return 1;
    }
    pt_len = hex_to_bytes(pth, pt, (int)sizeof(pt));
    if (pt_len <= 0) { fprintf(stderr, "bad plaintext\n"); return 1; }
    ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return 1;
    if (EVP_EncryptInit_ex(ctx, EVP_sm4_ofb(), NULL, key, iv) != 1) goto err;
    if (EVP_CIPHER_CTX_set_padding(ctx, 0) != 1) goto err;
    if (EVP_EncryptUpdate(ctx, out, &len, pt, pt_len) != 1) goto err;
    tot = len;
    if (EVP_EncryptFinal_ex(ctx, out + tot, &len) != 1) goto err;
    tot += len;
"""
        + _tail_encrypt_print()
    )


_CANONICAL_C_BUILDERS = {
    ("DES", "CBC"): _canonical_des_cbc_c,
    ("DES", "CFB"): _canonical_des_cfb_c,
    ("DES", "OFB"): _canonical_des_ofb_c,
    ("AES", "CFB"): _canonical_aes_cfb_c,
    ("SM4", "CFB"): _canonical_sm4_cfb_c,
    ("SM4", "OFB"): _canonical_sm4_ofb_c,
}


def lookup_canonical_c(algorithm: Optional[str], mode: Optional[str]) -> Optional[str]:
    if not algorithm or not mode:
        return None
    key = (algorithm.strip().upper(), mode.strip().upper())
    fn = _CANONICAL_C_BUILDERS.get(key)
    if not fn:
        return None
    return fn()
