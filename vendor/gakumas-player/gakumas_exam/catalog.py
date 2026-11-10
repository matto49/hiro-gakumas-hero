"""主数据目录：gakumasu-diff 固定快照（data/master/<commit>/catalog.sqlite），只用标准库。"""
import gzip
import hashlib
import json,os
from pathlib import Path
import sqlite3
import zlib
from .errors import Unsupported

ROOT=Path(__file__).resolve().parents[1]


class Catalog:
    def __init__(self):
        self.pointer=json.loads((ROOT/'data/master/current.json').read_text())
        # 环境变量 GAKUMAS_MASTER_COMMIT 可选用其他已构建的快照（例如用旧版本数据对账旧对局）
        override=os.environ.get('GAKUMAS_MASTER_COMMIT')
        if override and override!=self.pointer['commit']:
            info=json.loads((ROOT/'data/master'/override/'build.json').read_text())
            self.pointer=dict(commit=override,database=override+'/catalog.sqlite',manifest_sha256=info['manifest_sha256'],
                              database_sha256=info['database_sha256'])
        self.directory=ROOT/'data/master'/self.pointer['commit']
        self.path=self.directory/'catalog.sqlite'
        self.db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
        self._cache={}
        self.info=json.loads(self.db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        if self.info['commit']!=self.pointer['commit']:raise Unsupported('Catalog/pointer version mismatch')

    @staticmethod
    def decode(payload):return json.loads(zlib.decompress(payload))

    def rows(self,table):
        for (payload,) in self.db.execute('SELECT payload FROM records WHERE table_name=? ORDER BY ordinal',(table,)):
            yield self.decode(payload)

    def one(self,table,key,variant=None):
        identity=(table,str(key),variant)
        if identity in self._cache:return self._cache[identity]
        sql='SELECT payload FROM records WHERE table_name=? AND row_id=?';args=[table,str(key)]
        if variant is not None:sql+=' AND variant=?';args.append(variant)
        values=self.db.execute(sql,args).fetchmany(2)
        if len(values)!=1:raise Unsupported(f'{table}:{key} needs an exact existing variant (matches {len(values)})')
        value=self.decode(values[0][0])
        if len(self._cache)>=4096:self._cache.pop(next(iter(self._cache)))
        self._cache[identity]=value
        return value

    def search(self,query='',limit=30):
        columns=['card_id','upgrade_count','name','rarity','plan','category','hidden']
        rows=self.db.execute('SELECT '+','.join(columns)+' FROM cards WHERE instr(name,?)>0 OR instr(card_id,?)>0 ORDER BY card_id,upgrade_count LIMIT ?',
                             (query,query,max(1,min(limit,10000))))
        return [dict(zip(columns,row)) for row in rows]

    def card(self,card_id,upgrade=0):return self.one('ProduceCard',card_id,upgrade)

    def by_id(self,table,key):
        return [self.decode(r[0]) for r in self.db.execute('SELECT payload FROM records WHERE table_name=? AND row_id=? ORDER BY ordinal',(table,str(key)))]

    def inspect_card(self,card_id,upgrade=0):
        row=self.card(card_id,upgrade)
        effects=[];triggers=[]
        for entry in row['playEffects']:
            effects.append(self.one('ProduceExamEffect',entry['produceExamEffectId']))
            if entry['produceExamTriggerId']:triggers.append(self.one('ProduceExamTrigger',entry['produceExamTriggerId']))
        if row['playProduceExamTriggerId']:triggers.append(self.one('ProduceExamTrigger',row['playProduceExamTriggerId']))
        custom=[r for key in row['produceCardCustomizeIds'] for r in self.by_id('ProduceCardCustomize',key)]
        grow={key:self.one('ProduceCardGrowEffect',key) for c in custom for key in c['produceCardGrowEffectIds']}
        return dict(commit=self.info['commit'],card=row,play_effects=effects,triggers=triggers,customize=custom,grow_effects=grow,
                    authority='community-extracted snapshot; current card availability and client version not inferred')

    def modes(self):
        return [dict(produce=r,group=next((g for g in self.rows('ProduceGroup') if r['id'] in g['produceIds']),None),
                     setting=self.one('ProduceSetting',r['produceSettingId'])) for r in self.rows('Produce')]

    def audit_references(self):
        tables=['ProduceExamEffect','ProduceExamTrigger','ProduceCardCustomize','ProduceCardGrowEffect',
                'ProduceCardStatusEnchant','ProduceExamStatusEnchant']
        ids={table:{r['id'] for r in self.rows(table)} for table in tables}
        checked=0;missing=[]
        def check(table,key,origin):
            nonlocal checked
            if not key:return
            checked+=1
            if key not in ids[table]:missing.append(dict(origin=origin,table=table,key=key))
        for card in self.rows('ProduceCard'):
            origin=card['id']+'@'+str(card['upgradeCount'])
            check('ProduceExamTrigger',card['playProduceExamTriggerId'],origin)
            check('ProduceCardStatusEnchant',card['produceCardStatusEnchantId'],origin)
            for item in card['playEffects']:
                check('ProduceExamEffect',item['produceExamEffectId'],origin)
                check('ProduceExamTrigger',item['produceExamTriggerId'],origin)
            for key in card['produceCardCustomizeIds']:check('ProduceCardCustomize',key,origin)
            for key in card['moveProduceExamEffectIds']:check('ProduceExamEffect',key,origin)
            for key in card['moveProduceExamTriggerIds']:check('ProduceExamTrigger',key,origin)
        for row in self.rows('ProduceCardCustomize'):
            for key in row['produceCardGrowEffectIds']:check('ProduceCardGrowEffect',key,row['id'])
        return dict(checked=checked,missing=missing,matches=not missing,
                    scope='card play/move triggers, effects, passive enchant, customize and grow-effect references')

    def verify(self):
        manifest_path=self.directory/'manifest.json';manifest=json.loads(manifest_path.read_text());errors=[]
        def digest(b):return hashlib.sha256(b).hexdigest()
        if digest(self.path.read_bytes())!=self.pointer['database_sha256']:errors.append('catalog database hash')
        if digest(manifest_path.read_bytes())!=self.pointer['manifest_sha256']:errors.append('manifest hash')
        for f in manifest['files']:
            compressed=(self.directory/f['stored']).read_bytes()
            if digest(compressed)!=f['gzip_sha256']:errors.append(f['name']+':compressed hash')
            raw=gzip.decompress(compressed)
            if digest(raw)!=f['sha256']:errors.append(f['name']+':raw hash')
            blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            if blob!=f['git_blob_sha']:errors.append(f['name']+':git blob hash')
        integrity=self.db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':errors.append('sqlite integrity:'+integrity)
        return dict(matches=not errors,errors=errors,files=len(manifest['files']),commit=self.info['commit'],sqlite_integrity=integrity)
