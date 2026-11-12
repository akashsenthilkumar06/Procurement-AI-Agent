"""Versioned repository boundary. Replace the implementation, not the tool API, for ERP exports."""
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

DATA_ROOT=Path(__file__).resolve().parent.parent/'datasets'
SLUG=re.compile(r'^[a-zA-Z0-9_-]{1,80}$')


class DataRepository(Protocol):
    version: str
    digest: str
    manifest: dict
    def records(self, table: str, role: str, access: dict) -> list[dict]: ...
    def hidden_values(self, role: str, access: dict) -> list: ...


class FileRepository:
    def __init__(self,dataset_id,version,root=None):
        if not SLUG.fullmatch(dataset_id) or not SLUG.fullmatch(version):raise ValueError('Invalid dataset identifier')
        self.path=(root or DATA_ROOT)/dataset_id/version
        raw=(self.path/'manifest.json').read_bytes();self.manifest=json.loads(raw)
        if self.manifest['id']!=dataset_id or self.manifest['version']!=version:raise ValueError('Dataset identity mismatch')
        self.version=dataset_id+'@'+version
        digest=hashlib.sha256(raw);self.tables={}
        for name,table in sorted(self.manifest['tables'].items()):
            file=table['file']
            if Path(file).name!=file or not file.endswith(('.csv','.json')):raise ValueError('Dataset file must be local CSV/JSON')
            content=(self.path/file).read_bytes();digest.update(name.encode());digest.update(content)
            if file.endswith('.json'):rows=json.loads(content)
            else:
                rows=list(csv.DictReader(content.decode().splitlines()))
                for row in rows:
                    for key,value in row.items():
                        if table.get('types',{}).get(key)=='number':row[key]=float(value)
            if not isinstance(rows,list) or len(rows)>10000 or any(not isinstance(r,dict) for r in rows):raise ValueError('Invalid dataset rows')
            if any(set(r)-set(table['fields']) for r in rows):raise ValueError('Every dataset field needs an access rule')
            self.tables[name]=rows
        self.digest=digest.hexdigest()

    def records(self,table,role,access):
        if table not in self.tables or table not in access:raise PermissionError('Table access denied')
        allowed=set(access[table]);fields=self.manifest['tables'][table]['fields']
        return [{key:value for key,value in row.items() if key in allowed and ('public' in fields[key] or role in fields[key])} for row in self.tables[table]]

    def hidden_values(self,role,access):
        # Values of restricted fields remain protected even if explicitly visible to the agent.
        return [value for name,rows in self.tables.items() for row in rows for key,value in row.items()
                if 'public' not in self.manifest['tables'][name]['fields'][key]]


def open_repository(dataset_id,version):
    return FileRepository(dataset_id,version)
