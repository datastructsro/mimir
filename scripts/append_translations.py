import json
from pathlib import Path

TRANSLATIONS_PATH = Path(__file__).with_name("translations.json")
I18N_DIR = TRANSLATIONS_PATH.parent.parent / "odoo_addon" / "i18n"

translations = json.loads(TRANSLATIONS_PATH.read_text(encoding="utf-8"))

for po_path in sorted(I18N_DIR.glob("*.po")):
    lang = po_path.stem
    updates = translations.get(lang)
    if not updates:
        continue

    content = po_path.read_text(encoding="utf-8")
    for msgid, msgstr in updates.items():
        pattern = f'msgid {json.dumps(msgid, ensure_ascii=False)}\nmsgstr ""'
        replacement = f"msgid {json.dumps(msgid, ensure_ascii=False)}\nmsgstr {json.dumps(msgstr, ensure_ascii=False)}"
        content = content.replace(pattern, replacement)
    po_path.write_text(content, encoding="utf-8")
    print(f"Updated {po_path}")
