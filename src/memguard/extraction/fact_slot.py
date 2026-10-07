from dataclasses import dataclass
import re


_PREDICATES = {
    "发布日期": "release_date",
    "发布日": "release_date",
    "上线日期": "launch_date",
    "负责人": "owner",
    "部署路径": "deployment_path",
    "安装路径": "install_path",
    "版本号": "version",
    "版本": "version",
}
_DISPLAY_PREDICATES = {
    "release_date": "发布日",
    "launch_date": "上线日期",
    "owner": "负责人",
    "deployment_path": "部署路径",
    "install_path": "安装路径",
    "version": "版本",
}
_UPDATE_VERBS = {"改为", "更改为", "更新为", "变更为", "调整为", "更正为"}
_PREDICATE_PATTERN = "|".join(sorted(_PREDICATES, key=len, reverse=True))
_VERB_PATTERN = "|".join(sorted((*_UPDATE_VERBS, "定于", "是", "为"), key=len, reverse=True))
_FACT_PATTERN = re.compile(
    rf"(?P<subject>[\w\u4e00-\u9fff-]{{2,64}}?)(?:的)?"
    rf"(?P<predicate>{_PREDICATE_PATTERN})(?P<verb>{_VERB_PATTERN})(?P<value>.+)"
)
_GENERIC_SUBJECTS = {"项目", "系统", "应用", "服务", "这个项目", "该项目", "我的项目"}


@dataclass(frozen=True)
class FactSlot:
    subject: str
    predicate: str
    value: str
    explicit_update: bool

    @property
    def canonical_content(self) -> str:
        return f"{self.subject}{_DISPLAY_PREDICATES[self.predicate]}是{self.value}"


def parse_fact_slot(text: str) -> FactSlot | None:
    value = str(text or "").strip().rstrip("。.!！")
    value = re.sub(r"^(?:请)?(?:记住|记录|记下)\s*[:：]?\s*", "", value)
    if not value or len(value) > 240:
        return None
    match = _FACT_PATTERN.fullmatch(value)
    if match is None:
        return None
    subject = match.group("subject").strip().lower()
    predicate = _PREDICATES[match.group("predicate")]
    fact_value = match.group("value").strip()
    if (
        subject in _GENERIC_SUBJECTS
        or not fact_value
        or len(fact_value) > 160
        or any(marker in fact_value for marker in "，,。；;\n")
        or fact_value.startswith(("不", "未", "无"))
    ):
        return None
    return FactSlot(subject, predicate, fact_value, match.group("verb") in _UPDATE_VERBS)
