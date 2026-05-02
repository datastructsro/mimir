import glob
import json


def find_missing_translations():
    po_files = glob.glob('odoo_addon/i18n/*.po')
    missing = set()

    # A simple state machine to parse PO files
    for filepath in po_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        current_msgid = None
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('msgid '):
                # Extract the string inside quotes
                current_msgid = line[6:].strip('"')
            elif line.startswith('msgstr '):
                msgstr = line[7:].strip('"')
                if current_msgid and current_msgid != "" and msgstr == "":
                    # Missing translation found
                    missing.add(current_msgid)
                current_msgid = None

    print(json.dumps(list(missing), indent=2))

if __name__ == '__main__':
    find_missing_translations()
