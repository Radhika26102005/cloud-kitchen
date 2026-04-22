import os
import glob

for f in glob.glob('templates/*.html'):
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    # Fix the broken JS literals
    new_content = content.replace('₹{', '${')
    
    if new_content != content:
        print(f"Fixed JS literals in {f}")
        with open(f, 'w', encoding='utf-8') as file:
            file.write(new_content)
