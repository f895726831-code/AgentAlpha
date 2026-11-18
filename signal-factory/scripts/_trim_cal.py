import sys
lines = []
with open('D:/qlib/qlib_data/cn_data/calendars/day.txt', 'r') as f:
    for line in f:
        line = line.strip()
        if line and line <= '2026-08-14':
            lines.append(line)
with open('D:/qlib/qlib_data/cn_data/calendars/day.txt', 'w') as f:
    for l in lines:
        f.write(l + '\n')
print(f'Calendar trimmed to {len(lines)} days, last: {lines[-1]}')