#!/usr/bin/env python3
import sqlite3, sys, os, re
DB='chat.db'
if not os.path.exists(DB):
    print('ERROR: chat.db not found at', os.path.abspath(DB))
    sys.exit(2)
conn=sqlite3.connect(DB)
conn.row_factory=sqlite3.Row
cur=conn.cursor()
print('Database:', os.path.abspath(DB))
# PRAGMA info
try:
    cur.execute("PRAGMA user_version")
    print('PRAGMA user_version:', cur.fetchone()[0])
except Exception as e:
    print('PRAGMA user_version error:', e)
print('\nTables and row counts:')
cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
tables=cur.fetchall()
if not tables:
    print('  (no tables found)')
for t in tables:
    name=t['name']
    print('\n--', name)
    try:
        cur.execute(f"SELECT COUNT(*) as c FROM {name}")
        cnt=cur.fetchone()['c']
    except Exception as e:
        cnt=f'ERR: {e}'
    print('  rows:', cnt)
    # schema / columns
    try:
        cur.execute(f"PRAGMA table_info('{name}')")
        cols=cur.fetchall()
        print('  columns:')
        col_names=[c['name'] for c in cols]
        for c in cols:
            print('   -', c['name'], c['type'], 'pk' if c['pk'] else '')
    except Exception as e:
        print('  PRAGMA table_info error:', e)
    # indexes
    try:
        cur.execute(f"PRAGMA index_list('{name}')")
        idxs=cur.fetchall()
        if idxs:
            print('  indexes:')
            for ix in idxs:
                print('   -', ix['name'], 'unique' if ix['unique'] else '')
                try:
                    cur.execute(f"PRAGMA index_info('{ix['name']}')")
                    parts=[r['name'] for r in cur.fetchall()]
                    print('     ->', parts)
                except Exception:
                    pass
    except Exception as e:
        print('  PRAGMA index_list error:', e)
    # sample rows
    try:
        cur.execute(f"SELECT * FROM {name} LIMIT 10")
        rows=cur.fetchall()
        if rows:
            print('  sample rows (masked):')
            for r in rows:
                out=[]
                for k in r.keys():
                    v=r[k]
                    sval=str(v) if v is not None else ''
                    # mask sensitive-looking columns
                    lk=k.lower()
                    if any(x in lk for x in ('password','pass','token','secret','key','sig','signature')):
                        sval='[MASKED]'
                    else:
                        # shorten long strings
                        if len(sval)>120:
                            sval=sval[:80]+'...[len='+str(len(sval))+']'
                        # redact likely private keys or long base64
                        if re.search(r'-----BEGIN ', sval) or re.search(r'^[A-Za-z0-9+/=]{120,}$', sval):
                            sval='[POTENTIAL_KEY_OR_BLOB len='+str(len(sval))+']'
                    out.append(f"{k}={sval}")
                print('   *', '; '.join(out))
    except Exception as e:
        print('  sample rows error:', e)

# quick sensitive-field scan across tables
print('\nQuick sensitive field scan:')
sensitive_patterns=['password','pass','token','secret','key','sig','signature','fcm','payload']
for t in tables:
    name=t['name']
    try:
        cur.execute(f"PRAGMA table_info('{name}')")
        cols=[c['name'] for c in cur.fetchall()]
    except Exception:
        cols=[]
    matches=[c for c in cols if any(p in c.lower() for p in sensitive_patterns)]
    if matches:
        print(' -', name, '->', matches)

conn.close()
print('\nScan complete.')
