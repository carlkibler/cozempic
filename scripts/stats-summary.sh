#!/bin/bash
# Cozempic Stats Summary — clean table format
# Usage: ./scripts/stats-summary.sh

set -euo pipefail

MEMORY_DIR="${HOME}/.claude/projects/-Users-ruya-Documents-Advisor-Cozempic/memory"

cd "$MEMORY_DIR" && python3 -c "
import sqlite3, json
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

db = sqlite3.connect('stats.db')
db.row_factory = sqlite3.Row

# Live counters
counters = {}
for name in ['installs', 'auto_updates', 'prunes', 'saved_under_100k', 'saved_100k_500k', 'saved_500k_1m', 'saved_over_1m']:
    url_name = name.replace('_', '-')
    try:
        data = json.loads(urlopen(Request(f'https://api.counterapi.dev/v1/cozempic/{url_name}/', headers={'User-Agent': 'cozempic'}), timeout=3).read())
        counters[name] = data.get('count', 0)
    except:
        row = db.execute('SELECT value FROM counters WHERE name=? ORDER BY date DESC LIMIT 1', (name,)).fetchone()
        counters[name] = row['value'] if row else 0

# GitHub
try:
    gh_data = json.loads(urlopen(Request('https://api.github.com/repos/Ruya-AI/cozempic', headers={'User-Agent': 'cozempic'}), timeout=3).read())
    stars = gh_data.get('stargazers_count', 0)
    forks = gh_data.get('forks_count', 0)
except:
    row = db.execute('SELECT stars, forks FROM github_snapshots ORDER BY date DESC LIMIT 1').fetchone()
    stars = row['stars'] if row else 0
    forks = row['forks'] if row else 0

# Grand total from DB
pypi_row = db.execute('SELECT SUM(mirrors) as m FROM pypi_daily').fetchone()
pypi_m = (pypi_row['m'] or 0) + 2589  # pre-feb9 baseline
npm_row = db.execute('SELECT SUM(downloads) as d FROM npm_daily').fetchone()
npm = npm_row['d'] or 0
clones_row = db.execute('SELECT SUM(clones) as c FROM clones_daily WHERE is_rolling=0').fetchone()
clones = (clones_row['c'] or 0) + 1125  # pre-daily estimate
grand = pypi_m + npm + clones + forks

# Tokens saved
tokens = (counters.get('saved_under_100k', 0) * 50000
         + counters.get('saved_100k_500k', 0) * 300000
         + counters.get('saved_500k_1m', 0) * 750000
         + counters.get('saved_over_1m', 0) * 1500000)

if tokens >= 1_000_000_000:
    tok_str = f'{tokens / 1_000_000_000:.1f}B'
elif tokens >= 1_000_000:
    tok_str = f'{tokens / 1_000_000:.0f}M'
else:
    tok_str = f'{tokens / 1_000:.0f}K'

print()
print('| Metric | Value |')
print('|--------|-------|')
print(f'| **Grand total** | **{grand:,}** |')
print(f'| **Stars** | **{stars}** |')
print(f'| **Forks** | **{forks}** |')
print(f'| **Tracked installs** | **{counters.get(\"installs\", 0):,}** |')
print(f'| **Auto-updates** | **{counters.get(\"auto_updates\", 0):,}** |')
print(f'| **Global prunes** | **{counters.get(\"prunes\", 0):,}** |')
print(f'| **Tokens saved** | **{tok_str}** |')
print()

db.close()
"
