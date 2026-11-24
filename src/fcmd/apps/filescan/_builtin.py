"""filescan 内置敏感规则（纯数据，供 ``_rules.builtin_ruleset`` 使用）。

单独成文件是为了让 ``_rules`` 聚焦模型与表达式、并控制单文件行数；本模块只依赖
``_rules`` 的数据类与严重级常量，不导入 GUI 或第三方库。
"""

from __future__ import annotations

from fcmd.apps.filescan._rules import (
    MATCH_REGEX,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    Matcher,
    Rule,
)

__all__ = ["builtin_rules"]


def builtin_rules() -> tuple[Rule, ...]:
    """内置示例规则：覆盖常见个人信息、联系方式、财务与凭证。"""
    return (
        Rule(
            id="cn_id",
            name="身份证号",
            group="个人信息",
            severity=SEVERITY_HIGH,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"(?<![0-9A-Za-z])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Za-z])",
            ),
        ),
        Rule(
            id="cn_phone",
            name="手机号",
            group="联系方式",
            severity=SEVERITY_MEDIUM,
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        ),
        Rule(
            id="email",
            name="邮箱地址",
            group="联系方式",
            severity=SEVERITY_LOW,
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        ),
        Rule(
            id="bank_card",
            name="银行卡号",
            group="财务信息",
            severity=SEVERITY_HIGH,
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"(?<!\d)(?:62|4\d|5[1-5])\d{13,17}(?!\d)"),
        ),
        Rule(
            id="api_key",
            name="API Key / 访问令牌",
            group="凭证",
            severity=SEVERITY_HIGH,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-.]{12,}",
                case_sensitive=False,
            ),
        ),
        Rule(
            id="aws_key",
            name="AWS 访问密钥",
            group="凭证",
            severity=SEVERITY_CRITICAL,
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ),
        Rule(
            id="private_key",
            name="私钥头",
            group="凭证",
            severity=SEVERITY_CRITICAL,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----",
                case_sensitive=False,
            ),
        ),
        Rule(
            id="db_conn",
            name="数据库连接串",
            group="凭证",
            severity=SEVERITY_HIGH,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"\b(?:mysql|mariadb|postgres(?:ql)?|mongodb(?:\+srv)?|redis|rediss|mssql|oracle|amqp|clickhouse)://[^\s\"'<>]+",
                case_sensitive=False,
            ),
        ),
        Rule(
            id="password_assign",
            name="密码赋值",
            group="凭证",
            severity=SEVERITY_MEDIUM,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"\b(?:password|passwd|pwd)\b\s*[:=]\s*[\"']?[^\s\"',;]{6,}",
                case_sensitive=False,
            ),
        ),
        Rule(
            id="jwt_token",
            name="JWT 令牌",
            group="凭证",
            severity=SEVERITY_HIGH,
            matcher=Matcher(
                kind=MATCH_REGEX, pattern=r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
            ),
        ),
        Rule(
            id="password_hash",
            name="密码哈希（bcrypt / argon2）",
            group="凭证",
            severity=SEVERITY_MEDIUM,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"(?:\$2[abxy]\$\d{2}\$[./A-Za-z0-9]{40,}|\$argon2(?:id|i|d)\$v=\d+\$[^\s\"']{20,})",
            ),
        ),
        Rule(
            id="github_token",
            name="GitHub 访问令牌",
            group="凭证",
            severity=SEVERITY_CRITICAL,
            matcher=Matcher(
                kind=MATCH_REGEX, pattern=r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b"
            ),
        ),
        Rule(
            id="slack_token",
            name="Slack 访问令牌",
            group="凭证",
            severity=SEVERITY_HIGH,
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
        ),
        Rule(
            id="aliyun_ak",
            name="阿里云 AccessKey ID",
            group="凭证",
            severity=SEVERITY_CRITICAL,
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"\bLTAI[A-Za-z0-9]{12,20}\b"),
        ),
        Rule(
            id="credit_code",
            name="统一社会信用代码",
            group="企业信息",
            severity=SEVERITY_MEDIUM,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Za-z])",
            ),
        ),
        Rule(
            id="wechat_id",
            name="微信号",
            group="社交账号",
            severity=SEVERITY_LOW,
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"(?:微信号|wxid|wechat(?:\s*id)?)\s*[:：]\s*[A-Za-z][-_A-Za-z0-9]{5,19}",
                case_sensitive=False,
            ),
        ),
    )
