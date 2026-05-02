import os


def rename_files(directory):
    # Rename mimir_*.py and mimir_*.xml
    for root, dirs, files in os.walk(directory):
        for file in files:
            if 'mimir' in file:
                old_path = os.path.join(root, file)
                new_file = file.replace('mimir', 'mimir')
                new_path = os.path.join(root, new_file)
                os.rename(old_path, new_path)
                print(f"Renamed {old_path} -> {new_path}")

def replace_in_files(directories_and_files):
    for path in directories_and_files:
        if os.path.isfile(path):
            files = [path]
        else:
            files = []
            for root, _, fs in os.walk(path):
                for f in fs:
                    if not f.endswith('.pyc') and not f.endswith('.pyo') and not f.endswith('~') and '.git' not in root and '.venv' not in root and '.ruff_cache' not in root:
                        files.append(os.path.join(root, f))

        for file_path in files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                continue # Skip binary or sqlite files

            # Perform replacements
            new_content = content.replace('mimir', 'mimir')
            new_content = new_content.replace('Mimir', 'Mimir')
            new_content = new_content.replace('MIMIR', 'MIMIR')

            if new_content != content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Updated content in {file_path}")

if __name__ == '__main__':
    # 1. Rename src/mimir directory
    if os.path.exists('src/mimir'):
        os.rename('src/mimir', 'src/mimir')
        print("Renamed src/mimir -> src/mimir")

    # 2. Rename files in odoo_addon
    rename_files('odoo_addon')

    # 3. Replace content in files
    targets = [
        'src/mimir',
        'tests',
        'odoo_addon',
        'scripts',
        'pyproject.toml',
        'README.md',
        'AGENTS.md',
        'GEMINI.md'
    ]
    replace_in_files(targets)
