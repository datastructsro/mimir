import os
import glob
import json

def is_untranslatable(text):
    if text.startswith('/'): return True
    if text.startswith('http'): return True
    if text.startswith('foreqcast.'): return True
    if text.startswith('res.config'): return True
    if text.startswith('parqcast-'): return True
    if len(text) == 36 and text.count('-') == 4: return True
    if text in ['eu-central-1']: return True
    return False

def translate_po_files():
    po_files = glob.glob('odoo_addon/i18n/*.po')
    
    # Load manual translations
    try:
        with open('scripts/translations.json', 'r', encoding='utf-8') as f:
            translations_db = json.load(f)
    except FileNotFoundError:
        translations_db = {}
    
    for filepath in po_files:
        basename = os.path.basename(filepath)
        lang = basename.replace('.po', '')
        
        # If we have manual translations for this lang, use them
        lang_dict = translations_db.get(lang, {})
        
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        new_lines = []
        i = 0
        translated_count = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('msgid "') and line.strip() != 'msgid ""':
                msgid = line[7:].strip().strip('"')
                
                next_i = i + 1
                while next_i < len(lines) and not lines[next_i].startswith('msgstr "'):
                    next_i += 1
                
                if next_i < len(lines):
                    msgstr_line = lines[next_i]
                    msgstr = msgstr_line[8:].strip().strip('"')
                    
                    if msgstr == "" and msgid != "":
                        if is_untranslatable(msgid):
                            translated = msgid
                        else:
                            # Use manual translation if available, else keep empty
                            translated = lang_dict.get(msgid, "")
                            
                        if translated:
                            if translated != msgid:
                                new_lines.append('#, fuzzy\n')
                            new_lines.append(line)
                            for j in range(i + 1, next_i):
                                new_lines.append(lines[j])
                            new_lines.append(f'msgstr "{translated}"\n')
                            translated_count += 1
                        else:
                            new_lines.append(line)
                            for j in range(i + 1, next_i + 1):
                                new_lines.append(lines[j])
                    else:
                        new_lines.append(line)
                        for j in range(i + 1, next_i + 1):
                            new_lines.append(lines[j])
                    i = next_i
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
            i += 1
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            
        if translated_count > 0:
            print(f"Injected {translated_count} translations for {lang}")

if __name__ == '__main__':
    translate_po_files()
