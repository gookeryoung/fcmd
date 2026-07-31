"""cryptool - 对称加密工具。

基于 Python 标准库（``hashlib``/``hmac``/``os``）实现的对称加密，无需外部依赖。

加密方案
--------
采用 **HMAC-SHA256 CTR 模式 + Encrypt-then-MAC** 构造，基于标准密码学原语组合：

1. **密钥派生**：``PBKDF2-HMAC-SHA256``（100_000 次迭代）从密码派生 64 字节
   密钥材料，拆分为 32 字节加密密钥 + 32 字节 MAC 密钥。
2. **加密**：以 ``HMAC-SHA256`` 作为伪随机函数（PRF）的 CTR 模式生成密钥流，
   与明文 XOR 得到密文。``HMAC`` 作为 PRF 在标准模型下可证明安全。
3. **认证**：``HMAC-SHA256`` 对 ``nonce + ciphertext`` 计算认证标签
   （Encrypt-then-MAC 构造，解密时先验证标签再解密，使用 ``hmac.compare_digest``
   恒定时间比较，抵抗时序攻击）。
4. **随机源**：``os.urandom`` 生成 nonce 与盐（密码学安全随机数）。

密文格式（url-safe base64 编码）：

- **密码模式**：``salt(16) + nonce(16) + ciphertext + tag(32)``
- **密钥模式**：``nonce(16) + ciphertext + tag(32)``

示例
----
    fcmd cryptool genkey                                   # 生成密钥
    fcmd cryptool encrypt "hello" --key <key>              # 用密钥加密
    fcmd cryptool decrypt "<blob>" --key <key>             # 用密钥解密
    fcmd cryptool encrypt "hello" --password 123           # 用密码加密
    fcmd cryptool decrypt "<blob>" --password 123          # 用密码解密
    fcmd cryptool encrypt "hello" --env  # 用环境变量密码加密
    fcmd cryptool decrypt "<blob>" --env  # 用环境变量密码解密
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

import fcmd

__all__ = ["Cryptool"]

# PBKDF2 参数
_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 16

# CTR 模式参数
_NONCE_BYTES = 16
_BLOCK_SIZE = 32  # HMAC-SHA256 输出长度（字节）
_TAG_BYTES = 32  # HMAC-SHA256 认证标签长度
_KEY_BYTES = 32  # 加密密钥 / MAC 密钥长度


# ============================================================================
# 公共类
# ============================================================================


class Cryptool:
    """加密密工具。"""

    @property
    def random_key(self) -> str:
        """生成随机密钥（64 字节，url-safe base64 编码）。

        Returns
        -------
        str
            url-safe base64 编码的 64 字节密钥；前 32 字节做加密密钥，后 32 字节做 MAC 密钥
        """

        return base64.urlsafe_b64encode(os.urandom(_KEY_BYTES * 2)).decode("ascii")

    def get_derive_keys(self, password: str, salt: bytes) -> tuple[bytes, bytes]:
        """用 PBKDF2-HMAC-SHA256 从密码派生加密密钥与 MAC 密钥。

        Parameters
        ----------
        password:
            用户密码
        salt:
            盐值（建议 16 字节）

        Returns
        -------
        tuple[bytes, bytes]
            ``(encryption_key, mac_key)``，各 32 字节
        """
        material = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_BYTES * 2
        )
        return material[:_KEY_BYTES], material[_KEY_BYTES:]

    def encrypt(self, plaintext: str, enc_key: bytes, mac_key: bytes) -> str:
        """加密文本（使用密钥）。

        Parameters
        ----------
        plaintext:
            待加密的明文
        enc_key:
            加密密钥（32 字节）
        mac_key:
            MAC 密钥（32 字节）

        Returns
        -------
        str
            url-safe base64 编码的 ``nonce + ciphertext + tag``
        """
        blob = _encrypt_bytes(plaintext.encode("utf-8"), enc_key, mac_key)
        return base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, token: str, enc_key: bytes, mac_key: bytes) -> str:
        """解密文本（使用密钥）。

        Parameters
        ----------
        token:
            url-safe base64 编码的密文（由 :func:`encrypt` 产生）
        enc_key:
            加密密钥（32 字节）
        mac_key:
            MAC 密钥（32 字节）

        Returns
        -------
        str
            解密后的明文

        Raises
        ------
        ValueError
            密文无效或认证失败时
        """
        blob = _b64decode(token)
        plaintext = _decrypt_bytes(blob, enc_key, mac_key)
        return _decode_utf8(plaintext)

    def encrypt_with_password(self, plaintext: str, password: str) -> str:
        """加密文本（使用密码，随机盐混入密文）。

        Parameters
        ----------
        plaintext:
            待加密的明文
        password:
            加密密码

        Returns
        -------
        str
            url-safe base64 编码的 ``salt + nonce + ciphertext + tag``
        """
        salt = os.urandom(_SALT_BYTES)
        enc_key, mac_key = self.get_derive_keys(password, salt)
        blob = _encrypt_bytes(plaintext.encode("utf-8"), enc_key, mac_key)
        return base64.urlsafe_b64encode(salt + blob).decode("ascii")

    def decrypt_with_password(self, blob: str, password: str) -> str:
        """解密文本（使用密码）。

        Parameters
        ----------
        blob:
            url-safe base64 编码的 ``salt + nonce + ciphertext + tag``（由
            :func:`encrypt_with_password` 产生）
        password:
            解密密码

        Returns
        -------
        str
            解密后的明文

        Raises
        ------
        ValueError
            密文无效或认证失败时
        """
        raw = _b64decode(blob)
        if len(raw) < _SALT_BYTES + _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("密文过短，无法提取盐值")
        salt = raw[:_SALT_BYTES]
        enc_key, mac_key = self.get_derive_keys(password, salt)
        plaintext = _decrypt_bytes(raw[_SALT_BYTES:], enc_key, mac_key)
        return _decode_utf8(plaintext)


_cryptool = Cryptool()


# ============================================================================
# 内部辅助
# ============================================================================


def _parse_key(key: str) -> tuple[bytes, bytes]:
    """解析 url-safe base64 编码的密钥为 ``(enc_key, mac_key)``。"""
    raw = _b64decode(key)
    if len(raw) != _KEY_BYTES * 2:
        raise ValueError(f"密钥长度应为 {_KEY_BYTES * 2} 字节，实际 {len(raw)} 字节")
    return raw[:_KEY_BYTES], raw[_KEY_BYTES:]


def _ctr_crypt(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    """HMAC-SHA256 CTR 模式加解密（XOR 对称）。

    以 ``nonce`` 为计数器起始值，每块用 ``HMAC-SHA256(enc_key, counter)`` 生成
    32 字节密钥流，与数据块 XOR。计数器按 16 字节大端序递增。
    """
    result = bytearray()
    counter = int.from_bytes(nonce, "big")
    for offset in range(0, len(data), _BLOCK_SIZE):
        counter_bytes = counter.to_bytes(_NONCE_BYTES, "big")
        keystream = hmac.new(enc_key, counter_bytes, hashlib.sha256).digest()
        block = data[offset : offset + _BLOCK_SIZE]
        # int XOR 后转回字节：处理末块不足 32 字节的情况
        xored = int.from_bytes(block, "big") ^ int.from_bytes(keystream[: len(block)], "big")
        result.extend(xored.to_bytes(len(block), "big"))
        counter += 1
    return bytes(result)


def _encrypt_bytes(plaintext: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
    """加密字节序列（返回 ``nonce + ciphertext + tag``）。"""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = _ctr_crypt(plaintext, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return nonce + ciphertext + tag


def _decrypt_bytes(blob: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
    """解密字节序列（输入 ``nonce + ciphertext + tag``）。

    先验证认证标签（Encrypt-then-MAC，恒定时间比较），通过后再解密。
    """
    min_len = _NONCE_BYTES + _TAG_BYTES
    if len(blob) < min_len:
        raise ValueError("密文过短，无法解析")
    nonce = blob[:_NONCE_BYTES]
    tag = blob[-_TAG_BYTES:]
    ciphertext = blob[_NONCE_BYTES:-_TAG_BYTES]
    # Encrypt-then-MAC：先验证标签（恒定时间比较，抵抗时序攻击）
    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("认证失败（密钥错误或密文损坏）")
    # 验证通过后解密
    return _ctr_crypt(ciphertext, enc_key, nonce)


def _b64decode(text: str) -> bytes:
    """url-safe base64 解码，失败时抛 ``ValueError``。"""
    try:
        return base64.urlsafe_b64decode(text.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"无效的 base64 格式: {exc}") from exc


def _decode_utf8(data: bytes) -> str:
    """UTF-8 解码，失败时抛 ``ValueError``。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"解密成功但非有效 UTF-8 文本: {exc}") from exc


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("cryptool", subcommand="genkey", help="生成加密密钥")
def genkey_cmd() -> None:
    """生成一个新的 64 字节随机密钥并打印（url-safe base64 编码）。"""
    print(_cryptool.random_key)


@fcmd.tool("cryptool", subcommand="encrypt", help="加密文本")
def encrypt_cmd(text: str, password: str = "", key: str = "", env: bool = False) -> None:
    """加密文本（需指定 ``--password`` 或 ``--key``，二选一）。

    Parameters
    ----------
    text:
        待加密的明文
    password:
        加密密码（与 ``key`` 二选一）
    key:
        加密密钥（与 ``password`` 二选一）
    """
    if env:
        password = os.getenv("FCMD_CRYPT_PASSWORD") or ""
        key = os.getenv("FCMD_CRYPT_KEY") or ""

    if not password and not key:
        print("请指定 --password 或 --key")
        return

    try:
        if key:
            enc_key, mac_key = _parse_key(key)
            result = _cryptool.encrypt(text, enc_key, mac_key)
        else:
            result = _cryptool.encrypt_with_password(text, password)
    except ValueError as exc:
        print(str(exc))
        return
    else:
        print(f"加密结果: {result}")


@fcmd.tool("cryptool", subcommand="decrypt", help="解密文本")
def decrypt_cmd(text: str, password: str = "", key: str = "", env: bool = False) -> None:
    """解密文本（需指定 ``--password`` 或 ``--key``，二选一）。

    Parameters
    ----------
    text:
        待解密的密文
    password:
        解密密码（与 ``key`` 二选一）
    key:
        加密密钥（与 ``password`` 二选一）
    use_env:
        是否从环境变量中获取密码和密钥（默认 False）
    """
    if env:
        password = os.getenv("FCMD_CRYPT_PASSWORD") or ""
        key = os.getenv("FCMD_CRYPT_KEY") or ""

    if not password and not key:
        print("请指定 --password 或 --key")
        return

    try:
        if key:
            enc_key, mac_key = _parse_key(key)
            result = _cryptool.decrypt(text, enc_key, mac_key)
        else:
            result = _cryptool.decrypt_with_password(text, password)
    except ValueError as exc:
        print(str(exc))
        return
    else:
        print(f"解密结果: {result}")


@fcmd.main("cryptool")
def main() -> None:
    pass


if __name__ == "__main__":
    main()
