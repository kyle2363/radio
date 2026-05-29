import requests
import time

r = requests.post('http://localhost:8765/api/play/17')
print('Play:', r.json())
time.sleep(15)
r = requests.get('http://localhost:8765/api/queue')
queue = r.json()
slots_found = {}
for item in queue:
    slots_found[item['slot']] = slots_found.get(item['slot'], 0) + 1
print('Queue:', len(queue), 'slots:', slots_found)
for item in queue:
    slot = item['slot']
    artist = item['artist']
    title = item['title']
    print(f'  {slot:10s} | {artist} - {title}')
