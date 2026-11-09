"""Build a lossless local catalog from a hash-verified pinned YAML snapshot.

PyYAML is a build-time dependency only. Runtime queries use stdlib sqlite/zlib.
"""
from pathlib import Path
import gzip
import hashlib
import json
import sqlite3
import sys
import time
import zlib
import yaml

ROOT=Path(__file__).resolve().parents[1]
# 用法：python3 scripts/build-master.py [commit] [--no-pointer]；--no-pointer 只建库、不切换 current.json
COMMIT=next((a for a in sys.argv[1:] if not a.startswith('-')),'c67b88ebfdf0a1654bd30facc746c6bb41f7bf0a')
UPDATE_POINTER='--no-pointer' not in sys.argv
SNAPSHOT=ROOT/'data/master'/COMMIT


def build():
    started=time.perf_counter()
    manifest=json.loads((SNAPSHOT/'manifest.json').read_text())
    target=SNAPSHOT/'catalog.sqlite';temp=SNAPSHOT/'catalog.building.sqlite'
    if temp.exists():temp.unlink()
    db=sqlite3.connect(temp)
    db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE records(table_name TEXT,row_id TEXT,variant INTEGER,ordinal INTEGER,payload BLOB NOT NULL,
                             PRIMARY KEY(table_name,ordinal));
        CREATE INDEX lookup ON records(table_name,row_id,variant);
        CREATE TABLE cards(card_id TEXT,upgrade_count INTEGER,name TEXT,rarity TEXT,plan TEXT,category TEXT,
                           hidden INTEGER,ordinal INTEGER,PRIMARY KEY(card_id,upgrade_count));
        CREATE INDEX card_names ON cards(name);
    ''')
    stats={};total=0;repairs=[]
    try:
        for source in manifest['files']:
            compressed=(SNAPSHOT/source['stored']).read_bytes()
            if hashlib.sha256(compressed).hexdigest()!=source['gzip_sha256']:raise ValueError('Compressed hash mismatch '+source['name'])
            raw=gzip.decompress(compressed)
            if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('Raw hash mismatch '+source['name'])
            # This exact upstream scalar is indented at mapping-key depth,
            # making Localization.yaml invalid YAML. Preserve original bytes;
            # audit the narrowly scoped parser repair separately.
            parsed=raw
            if source['name']=='Localization.yaml':
                old='  description: |\r  インターバル終了確認\n'.encode()
                new='  description: |\n    インターバル終了確認\n'.encode()
                if parsed.count(old)!=1:raise ValueError('Pinned Localization repair no longer matches')
                parsed=parsed.replace(old,new)
                repairs.append(dict(table='Localization',line=163,kind='indent malformed block scalar',
                                    source_sha256=source['sha256'],parsed_sha256=hashlib.sha256(parsed).hexdigest()))
            if source['name']=='Rule.yaml':
                if parsed.count(b'\x0b')!=12 or '\ue000'.encode() in parsed:raise ValueError('Pinned Rule control-character repair no longer matches')
                parsed=parsed.replace(b'\x0b','\ue000'.encode())
                repairs.append(dict(table='Rule',kind='temporarily mask 12 YAML-illegal vertical tabs; restore exact characters after parse',
                                    source_sha256=source['sha256'],parsed_sha256=hashlib.sha256(parsed).hexdigest()))
            try:rows=yaml.load(parsed,Loader=yaml.CSafeLoader) or []
            except yaml.YAMLError as error:raise ValueError(source['name']+': '+str(error)) from error
            if source['name']=='Rule.yaml':
                # JSON-escaped strings round-trip the control characters.
                rows=json.loads(json.dumps(rows).replace('\\ue000','\\u000b'))
            if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('Unexpected YAML shape '+source['name'])
            table=source['name'].removesuffix('.yaml')
            for ordinal,row in enumerate(rows):
                payload=json.dumps(row,ensure_ascii=False,separators=(',',':')).encode()
                db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(table,str(row.get('id','')),row.get('upgradeCount',-1),ordinal,zlib.compress(payload,6)))
                if table=='ProduceCard':
                    db.execute('INSERT INTO cards VALUES(?,?,?,?,?,?,?,?)',(row['id'],row['upgradeCount'],row['name'],row['rarity'],row['planType'],row['category'],row['libraryHidden'],ordinal))
            stats[table]=len(rows);total+=len(rows)
            db.commit()
            if len(stats)%25==0:print(json.dumps(dict(tables=len(stats),rows=total)),flush=True)
            del rows,raw,compressed
        info=dict(commit=COMMIT,source=manifest['repository'],tables=len(stats),rows=total,table_counts=stats,
                  card_base_ids=db.execute('SELECT COUNT(DISTINCT card_id) FROM cards').fetchone()[0],
                  card_variants=db.execute('SELECT COUNT(*) FROM cards').fetchone()[0],
                  raw_original_bytes=sum(s['bytes'] for s in manifest['files']),
                  build_seconds=time.perf_counter()-started,builder_python=sys.version,pyyaml_version=yaml.__version__,
                  parser_repairs=repairs,manifest_sha256=hashlib.sha256((SNAPSHOT/'manifest.json').read_bytes()).hexdigest())
        db.execute('INSERT INTO metadata VALUES(?,?)',('snapshot',json.dumps(info)))
        db.commit();db.close();temp.replace(target)
        info['database_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        info['database_bytes']=target.stat().st_size
        (SNAPSHOT/'build.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n')
        if UPDATE_POINTER:(ROOT/'data/master/current.json').write_text(json.dumps(dict(commit=COMMIT,database=COMMIT+'/catalog.sqlite',manifest_sha256=info['manifest_sha256'],database_sha256=info['database_sha256']),indent=2)+'\n')
        print(json.dumps({k:v for k,v in info.items() if k!='table_counts'},ensure_ascii=False),flush=True)
    except Exception:
        db.close()
        raise


if __name__=='__main__':build()
