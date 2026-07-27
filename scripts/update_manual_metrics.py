from pathlib import Path
import argparse

from docx import Document


REPLACEMENTS = {
    "openKylin 单元测试为 140 passed；本地单元测试为 140 passed、1 skipped": (
        "openKylin 单元测试为 142 passed；本地单元测试为 141 passed、1 skipped"
    ),
    "当前本地测试为 140 passed、1 skipped，openKylin 为 140 passed": (
        "当前本地测试为 141 passed、1 skipped，openKylin 为 142 passed"
    ),
    "10.844": "10.105",
    "106.095": "75.255",
    "149284": "130444",
    "133 passed": "140 passed",
    "通用评测与 Agent 专项": "通用评测、检索消融与 Agent 专项",
    "673 passed": "675 passed",
}


def iter_paragraphs(document):
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def update_document(source: Path, output: Path) -> int:
    document = Document(str(source))
    replacement_count = 0
    for paragraph in iter_paragraphs(document):
        for run in paragraph.runs:
            updated = run.text
            for old, new in REPLACEMENTS.items():
                occurrences = updated.count(old)
                if occurrences:
                    replacement_count += occurrences
                    updated = updated.replace(old, new)
            run.text = updated
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output))
    return replacement_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Update verified performance metrics in the Chinese operation manual")
    parser.add_argument("source")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    count = update_document(Path(args.source), Path(args.output))
    print("updated_occurrences=%s" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
