import xml.etree.ElementTree as ET
import glob
import os
import sys

def escape_pot_string(s):
    if not s:
        return ""
    # Remove leading/trailing whitespaces that might be artifacts of XML formatting
    s = s.strip()
    if not s:
        return ""
    # Escape quotes and newlines
    s = s.replace('\\', '\\\\')
    s = s.replace('"', '\\"')
    s = s.replace('\n', '\\n')
    return s

def extract_strings():
    translatable_attrs = {'string', 'help', 'confirm', 'placeholder', 'sum', 'avg'}
    strings_found = {} # msgid -> set of files

    for xml_file in glob.glob('odoo_addon/**/*.xml', recursive=True):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            for elem in root.iter():
                # Check attributes
                for attr in translatable_attrs:
                    if attr in elem.attrib:
                        val = escape_pot_string(elem.attrib[attr])
                        if val:
                            strings_found.setdefault(val, set()).add(xml_file)
                
                # Check <field name="name">Text</field>
                if elem.tag == 'field' and elem.attrib.get('name') == 'name':
                    if elem.text:
                        val = escape_pot_string(elem.text)
                        if val:
                            strings_found.setdefault(val, set()).add(xml_file)
                            
        except Exception as e:
            print(f"Error parsing {xml_file}: {e}", file=sys.stderr)

    # Print to stdout in POT format
    print('# Translation template for XML files')
    print('msgid ""')
    print('msgstr ""')
    print('"MIME-Version: 1.0\\n"')
    print('"Content-Type: text/plain; charset=UTF-8\\n"')
    print('"Content-Transfer-Encoding: 8bit\\n"')
    print()

    for msgid, files in sorted(strings_found.items()):
        for f in sorted(files):
            print(f"#: {f}")
        print(f'msgid "{msgid}"')
        print('msgstr ""')
        print()

if __name__ == '__main__':
    extract_strings()
