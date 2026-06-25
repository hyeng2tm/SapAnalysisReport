import os

print(f'Current directory: {os.getcwd()}')
print(f'Files in ./data:')
for f in os.listdir('./data'):
    print(f'  {f}')
print(f'\nFiles in data:')
if os.path.exists('data'):
    for f in os.listdir('data'):
        print(f'  {f}')
