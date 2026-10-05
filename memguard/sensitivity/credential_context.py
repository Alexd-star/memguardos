"""Local credential-value syntax, distinct from policy and definition predicates."""

from __future__ import annotations

import re


_NAME = (
    r"(?<![A-Za-z0-9_])(?:密码|口令|"
    r"(?:[A-Za-z][A-Za-z0-9_]*_)?(?:password|passwd|pwd|passcode|pin(?:[ \t]*(?:码|code))?)(?![A-Za-z_]))"
)
_GAP = r"\s*"
_OPERATOR = r"(?:设置为|设置成|设为|是|为|[:=：]|(?:is|equals)(?![A-Za-z_]))"
_VALUE = (
    r'''(?P<value>"[^"\r\n]{1,128}"|'[^'\r\n]{1,128}'|'''
    r"“[^”\r\n]{1,128}”|‘[^’\r\n]{1,128}’|"
    r'''[^\s,，;；。!?！？、"'“”‘’]{1,128})'''
)

PASSWORD_SECRET_PATTERN = re.compile(
    _NAME + _GAP + r"(?P<operator>" + _OPERATOR + r")" + _GAP + _VALUE,
    re.IGNORECASE,
)
PASSWORD_REMEMBER_PATTERN = re.compile(
    r"(?:记住|保存|登录|解锁|(?:remember|save|store|unlock|log[ \t]+in[ \t]+with)\b)"
    + _GAP + r"(?:(?:我的|这个|该)|(?:my|the|this)[ \t]+)?"
    + _GAP + _NAME + _GAP + r"(?P<operator>" + _OPERATOR + r")?" + _GAP + _VALUE,
    re.IGNORECASE,
)

_CLAUSE_END = re.compile(r"[,，;；。!?！？\r\n]")
_LENGTH_REQUIREMENT = re.compile(
    r"^(?:(?:at[ \t]+least|at[ \t]+most|exactly|between)[ \t]+|至少|不超过|最多)?"
    r"[0-9零一二三四五六七八九十百两]+"
    r"(?:[ \t]*(?:to|and|至|到|-)[ \t]*[0-9零一二三四五六七八九十百两]+)?"
    r"(?:[ \t]+(?:digits?|characters?|chars?|bytes?)\b|[ \t]*(?:位|个字符|字符|字节))",
    re.IGNORECASE,
)
_NONVALUE_EN = re.compile(
    r"^(?:not[ \t]+(?:yet[ \t]+)?(?:set|configured|provided|supplied|available|stored|defined|a[ \t]+secret)"
    r"|(?:unset|missing|unknown|required|optional|mandatory)"
    r"|(?:used|needed)[ \t]+(?:for|to)"
    r"|(?:must|should|needs?[ \t]+to)[ \t]+(?:be|contain|have|include|remain|change))\b",
    re.IGNORECASE,
)
_DEFINITION_EN = re.compile(
    r"^(?:(?:a|an|the)[ \t]+)(?:(?:required|important|optional|security|authentication|review|numeric|secret)[ \t]+)*"
    r"(?:string|code|credential|field|parameter|identifier|term|concept|requirement|mechanism|point|item)\b",
    re.IGNORECASE,
)
_NONVALUE_CN = re.compile(
    r"^(?:(?:尚未|还未|没有|未|不)(?:设置|配置|提供|填写|输入|保存|记录|启用|定义)"
    r"|(?:无需|不需要|不要|不得)(?:填写|保存|提供|记录|输出|输入|设置)"
    r"|(?:需要|必须|应当|应该)(?:定期)?(?:设置|配置|提供|更新|修改|轮换|复核|检查|验证)"
    r"|(?:一个|一种)?(?:用于|用来|用以|表示|指的是|指代)"
    r"|(?:本|该|此|这|当前)[\u4e00-\u9fff]{1,16}(?:必须|需要|应当|应该)"
    r"|(?:一个|一种)?(?:认证|登录|解锁|验证|访问)(?:用|所用)?的(?:字符串|代码|凭据|口令|要素)"
    r"|(?:一个|一种)?(?:重要|关键|核心|常见)?(?:字段|术语|概念|安全机制|配置项)(?:名称|含义|定义|要求|之一)?)"
)
_POLICY_SUBJECT = re.compile(
    r"^(?:策略|要求|规则|长度|格式|重置流程|policy\b|requirements?\b|rules?\b|length\b|format\b)",
    re.IGNORECASE,
)


def credential_value_is_present(text: str, match: re.Match[str]) -> bool:
    """Filter only this candidate's RHS; never allowlist its enclosing document."""
    if "value" not in match.re.groupindex:
        # Legacy weak-password findings use the same assignment semantics.
        parsed = PASSWORD_SECRET_PATTERN.match(text, match.start())
        if parsed is None:
            return True
        match = parsed
    value = match.group("value")
    operator = match.group("operator") or ""
    if value[0] in "\"'“‘" or operator in {":", "=", "："}:
        return True

    start = match.start("value")
    end = _CLAUSE_END.search(text, start)
    tail = text[start:end.start() if end else len(text)][:256]
    # An appended concrete assignment must not disappear behind a requirement.
    if re.search(r"[:=：][ \t]*\S", tail):
        return True
    if _LENGTH_REQUIREMENT.match(tail):
        return False
    if _NONVALUE_EN.match(tail) or _DEFINITION_EN.match(tail) or _NONVALUE_CN.match(tail):
        return False
    if not operator and _POLICY_SUBJECT.match(tail):
        return False
    return True


def is_code_assignment_key(match: re.Match[str]) -> bool:
    """New shorthand/namespaced keys retain an existing token-assignment label."""
    operator = match.group("operator") or ""
    key = match.string[match.start():match.start("operator")].strip().lower()
    return operator in {":", "=", "："} and (key == "pwd" or "_" in key)
