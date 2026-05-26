"""校验 test_data.yaml：DES/AES 用 PyCryptodome；AES-CTR/GCM 用 cryptography；RSA 为教材级无填充模幂；SM4 用 OpenSSL enc。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from binascii import hexlify, unhexlify
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "test_data.yaml"


def hx(s: str) -> bytes:
    s = str(s).strip().strip('"').replace(" ", "")
    return unhexlify(s)


def sm4_openssl(mode: str, pt: bytes, key: bytes, iv: bytes) -> bytes:
    """mode: ECB|CBC|CFB|OFB → openssl enc -sm4-{mode} -nopad"""
    m = mode.upper()
    alg = f"sm4-{m.lower()}"
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        pin, pout = tdp / "pt.bin", tdp / "ct.bin"
        pin.write_bytes(pt)
        cmd = [
            "openssl",
            "enc",
            f"-{alg}",
            "-nopad",
            "-K",
            key.hex(),
            "-in",
            str(pin),
            "-out",
            str(pout),
        ]
        if m != "ECB":
            cmd.extend(["-iv", iv.hex()])
        subprocess.run(cmd, check=True, capture_output=True)
        return pout.read_bytes()


def main() -> int:
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    errors: list[tuple[str, str, str]] = []
    ok: list[tuple[str, str]] = []

    from Cryptodome.Cipher import AES, DES

    # DES
    d = data["DES"]
    pt, key, iv = hx(d["plaintext"]), hx(d["key"]), hx(d["iv"])
    for mode_name, exp in d["ciphertexts"].items():
        exp_b = hx(exp)
        m = mode_name.upper()
        if m == "ECB":
            ct = DES.new(key, DES.MODE_ECB).encrypt(pt)
        elif m == "CBC":
            ct = DES.new(key, DES.MODE_CBC, iv).encrypt(pt)
        elif m == "CFB":
            ct = DES.new(key, DES.MODE_CFB, iv, segment_size=8).encrypt(pt)
        elif m == "OFB":
            ct = DES.new(key, DES.MODE_OFB, iv).encrypt(pt)
        else:
            continue
        if ct != exp_b:
            errors.append(
                (
                    "DES",
                    m,
                    f"expected={hexlify(exp_b).decode()} got={hexlify(ct).decode()}",
                )
            )
        else:
            ok.append(("DES", m))

    # AES ECB/CBC/CFB/OFB
    a = data["AES"]
    pt, key, iv = hx(a["plaintext"]), hx(a["key"]), hx(a["iv"])
    for mode_name, exp in a["ciphertexts"].items():
        exp_b = hx(exp)
        m = mode_name.upper()
        if m == "ECB":
            cipher = AES.new(key, AES.MODE_ECB)
        elif m == "CBC":
            cipher = AES.new(key, AES.MODE_CBC, iv)
        elif m == "CFB":
            cipher = AES.new(key, AES.MODE_CFB, iv, segment_size=8)
        elif m == "OFB":
            cipher = AES.new(key, AES.MODE_OFB, iv)
        else:
            continue
        ct = cipher.encrypt(pt)
        if ct != exp_b:
            errors.append(
                ("AES", m, f"expected={hexlify(exp_b).decode()} got={hexlify(ct).decode()}")
            )
        else:
            ok.append(("AES", m))

    # AES CTR / GCM
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    for mname, md in a["modes"].items():
        if mname == "CTR":
            pt2, key2, iv2 = hx(md["plaintext"]), hx(md["key"]), hx(md["iv"])
            exp2 = hx(md["ciphertext"])
            ctr = Cipher(
                algorithms.AES(key2), modes.CTR(iv2), backend=default_backend()
            ).encryptor()
            ct2 = ctr.update(pt2) + ctr.finalize()
            if ct2 != exp2:
                errors.append(("AES", "CTR", "ciphertext mismatch"))
            else:
                ok.append(("AES", "CTR"))
        elif mname == "GCM":
            pt2 = hx(md["plaintext"])
            key2 = hx(md["key"])
            nonce = hx(md["iv"])
            aad = hx(md["aad"])
            exp_full = hx(md["ciphertext_with_tag"])
            aesgcm = AESGCM(key2)
            got = aesgcm.encrypt(nonce, pt2, aad)
            if got != exp_full:
                errors.append(
                    (
                        "AES",
                        "GCM",
                        f"len got={len(got)} exp={len(exp_full)} "
                        f"head got={hexlify(got[:16]).decode()}",
                    )
                )
            else:
                ok.append(("AES", "GCM"))

    # RSA 教材向量：无 PKCS#1 填充，密文为 m^e mod n，固定为模长字节（与 yaml 一致）
    r = data["RSA"]
    pt = hx(r["plaintext"])
    n = int(r["public_key"]["n"], 16)
    e = int(r["public_key"]["e"], 16)
    d = int(r["private_key"]["d"], 16)
    exp_enc = hx(r["ciphertexts"]["encrypt"])
    m = int.from_bytes(pt, "big")
    if m >= n:
        errors.append(("RSA", "encrypt", "plaintext integer >= modulus"))
    else:
        k = (n.bit_length() + 7) // 8
        ct = pow(m, e, n).to_bytes(k, "big")
        if ct != exp_enc:
            errors.append(
                (
                    "RSA",
                    "encrypt",
                    f"expected={hexlify(exp_enc).decode()} got={hexlify(ct).decode()}",
                )
            )
        else:
            ok.append(("RSA", "encrypt"))
    exp_sig_hex = r["ciphertexts"]["sign"]
    sig_int = int(exp_sig_hex, 16)
    if pow(sig_int, e, n) != m:
        errors.append(("RSA", "sign", "signature^e mod n != plaintext message"))
    else:
        ok.append(("RSA", "sign"))

    # SM4：与 OpenSSL enc 对齐（仓库 C 评测常用 EVP/SM4 栈）
    s = data["SM4"]
    pt, key, iv = hx(s["plaintext"]), hx(s["key"]), hx(s["iv"])
    for mode_name, exp in s["ciphertexts"].items():
        exp_b = hx(exp)
        m = mode_name.upper()
        try:
            ct = sm4_openssl(m, pt, key, iv)
        except (subprocess.CalledProcessError, FileNotFoundError) as ex:
            errors.append(("SM4", m, f"openssl: {ex!r}"))
            continue
        if ct != exp_b:
            errors.append(
                ("SM4", m, f"expected={hexlify(exp_b).decode()} got={hexlify(ct).decode()}")
            )
        else:
            ok.append(("SM4", m))

    print("校验文件:", YAML_PATH)
    print("通过:", len(ok), ok)
    print("失败:", len(errors))
    for t in errors:
        print(" ", t)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
