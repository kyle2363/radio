import json, os, sys

base = r'C:\Users\kyleh\Desktop\data\projects\radio\docs\understand_new'
targets = ['Diviners', "it's all the same", 'Runaway Kids', 'Kiss Me', '5ever', 'At Once', 'dreaming about you']

files = {}
for f in os.listdir(base):
    if f.endswith('.json'):
        files[f.lower()] = os.path.join(base, f)

for t in targets:
    t_low = t.lower()
    found = False
    for k, v in files.items():
        if t_low in k:
            with open(v, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            lyr = data[0].get('lyrics', '')
            print(f'  {t}: len={len(lyr)} ok={len(lyr) > 20}', flush=True)
            found = True
            break
    if not found:
        print(f'  {t}: NOT FOUND ON DISK', flush=True)
