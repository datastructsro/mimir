#!/bin/bash
set -e

# Ensure we're in the project root
cd "$(dirname "$0")/.."

I18N_DIR="odoo_addon/i18n"
mkdir -p "$I18N_DIR"

echo "Extracting Python strings..."
find odoo_addon -name '*.py' > .pyfiles.tmp
if [ -s .pyfiles.tmp ]; then
    xgettext --language=Python --keyword=_ --from-code=UTF-8 -o "$I18N_DIR/python.pot" -f .pyfiles.tmp
else
    echo "msgid \"\"" > "$I18N_DIR/python.pot"
    echo "msgstr \"\"" >> "$I18N_DIR/python.pot"
fi
rm .pyfiles.tmp

echo "Extracting XML strings..."
python scripts/extract_xml_strings.py > "$I18N_DIR/xml.pot"

echo "Merging into foreqcast.pot..."
if [ -f "$I18N_DIR/python.pot" ] && [ -f "$I18N_DIR/xml.pot" ]; then
    # --use-first prevents duplicate errors during msgcat
    msgcat --use-first "$I18N_DIR/python.pot" "$I18N_DIR/xml.pot" -o "$I18N_DIR/foreqcast.pot"
elif [ -f "$I18N_DIR/python.pot" ]; then
    cp "$I18N_DIR/python.pot" "$I18N_DIR/foreqcast.pot"
elif [ -f "$I18N_DIR/xml.pot" ]; then
    cp "$I18N_DIR/xml.pot" "$I18N_DIR/foreqcast.pot"
fi

# Clean up
rm -f "$I18N_DIR/python.pot" "$I18N_DIR/xml.pot"

echo "Done generating $I18N_DIR/foreqcast.pot"
