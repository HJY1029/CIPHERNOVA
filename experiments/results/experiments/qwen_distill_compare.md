## Qwen 蒸馏前后对比（功能测试 FTPR）

- Provider: `qwen_coder_local`
- 算法范围: **DES+AES+RSA+SM4（全网格）**
- 网格格数: **45**
- 增量合并自: `qwen_distill_compare.json`
- 本次无蒸馏刷新: **SM4:ofb:python**；有蒸馏刷新: **SM4:ofb:python**（history_only）
- 历史复测跳过（无蒸馏 / 有蒸馏）: **0** / **45**（无蒸馏应恒为 0；有蒸馏 `HIST`=未调 LLM）
- 无蒸馏通过: **26 / 45**（57.8%）
- 有蒸馏通过: **45 / 45**（100.0%）
- 教师池 JSONL 条目: **2272**

| 算法 | 模式 | 语言 | 无蒸馏 FTPR | 有蒸馏 FTPR |
|------|------|------|:-------------:|:-------------:|
| DES | ECB | python | ✗ | ✓ |
| DES | ECB | c | ✗ | ✓ |
| DES | ECB | cpp | ✗ | ✓ |
| DES | CBC | python | ✓ | ✓ |
| DES | CBC | c | ✗ | ✓ |
| DES | CBC | cpp | ✓ | ✓ |
| DES | CFB | python | ✓ | ✓ |
| DES | CFB | c | ✓ | ✓ |
| DES | CFB | cpp | ✗ | ✓ |
| DES | OFB | python | ✗ | ✓ |
| DES | OFB | c | ✗ | ✓ |
| DES | OFB | cpp | ✗ | ✓ |
| AES | ECB | python | ✓ | ✓ |
| AES | ECB | c | ✓ | ✓ |
| AES | ECB | cpp | ✗ | ✓ |
| AES | CBC | python | ✓ | ✓ |
| AES | CBC | c | ✗ | ✓ |
| AES | CBC | cpp | ✓ | ✓ |
| AES | CFB | python | ✓ | ✓ |
| AES | CFB | c | ✓ | ✓ |
| AES | CFB | cpp | ✗ | ✓ |
| AES | OFB | python | ✓ | ✓ |
| AES | OFB | c | ✓ | ✓ |
| AES | OFB | cpp | ✗ | ✓ |
| AES | GCM | python | ✓ | ✓ |
| AES | GCM | c | ✗ | ✓ |
| AES | GCM | cpp | ✗ | ✓ |
| AES | CTR | python | ✗ | ✓ |
| AES | CTR | c | ✓ | ✓ |
| AES | CTR | cpp | ✗ | ✓ |
| RSA | — | python | ✓ | ✓ |
| RSA | — | c | ✓ | ✓ |
| RSA | — | cpp | ✓ | ✓ |
| SM4 | ECB | python | ✓ | ✓ |
| SM4 | ECB | c | ✓ | ✓ |
| SM4 | ECB | cpp | ✗ | ✓ |
| SM4 | CBC | python | ✓ | ✓ |
| SM4 | CBC | c | ✓ | ✓ |
| SM4 | CBC | cpp | ✗ | ✓ |
| SM4 | CFB | python | ✗ | ✓ |
| SM4 | CFB | c | ✓ | ✓ |
| SM4 | CFB | cpp | ✓ | ✓ |
| SM4 | OFB | python | ✓ | ✓ |
| SM4 | OFB | c | ✓ | ✓ |
| SM4 | OFB | cpp | ✓ | ✓ |

*UTC 2026-05-25 07:53:11*