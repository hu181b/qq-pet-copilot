"""Named profiles use immutable IDs; settings and runtime data never share a directory."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path


class ProfileStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.file = self.root / 'profiles.json'

    def read(self):
        if not self.file.exists():
            return {'active': 'default', 'profiles': {'default': '默认配置'}}
        data = json.loads(self.file.read_text(encoding='utf-8'))
        if 'default' not in data['profiles'] or data['active'] not in data['profiles']:
            raise ValueError('配置目录索引无效，请检查 profiles.json')
        for key in data['profiles']:
            self.directory(key)
        return data

    def directory(self, key):
        if key == 'default':
            return self.root
        if not re.fullmatch(r'[0-9a-f]{32}', key):
            raise ValueError('无效配置编号')
        return self.root / 'profiles' / key

    def save(self, data):
        tmp = self.file.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, self.file)

    def _name(self, data, name, except_id=None):
        name = name.strip()
        if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
            raise ValueError('名称需为 1–40 个字符，不能含换行或控制字符')
        if any(v.casefold() == name.casefold() for k, v in data['profiles'].items() if k != except_id):
            raise ValueError('已有同名配置，请换一个名称')
        return name

    def create(self, name, source):
        data = self.read()
        name = self._name(data, name)
        if source not in data['profiles']:
            raise ValueError('来源配置不存在')
        key = uuid.uuid4().hex
        target = self.directory(key)
        target.mkdir(parents=True)
        # Only copy settings. Statistics, logs and pending tasks start empty.
        shutil.copy2(self.directory(source) / 'config.yaml', target / 'config.yaml')
        data['profiles'][key] = name
        self.save(data)
        return key

    def rename(self, key, name):
        data = self.read()
        if key not in data['profiles']:
            raise ValueError('配置不存在')
        data['profiles'][key] = self._name(data, name, key)
        self.save(data)

    def select(self, key):
        data = self.read()
        if key not in data['profiles']:
            raise ValueError('配置不存在')
        if not (self.directory(key) / 'config.yaml').is_file():
            raise ValueError('配置文件丢失，无法切换')
        data['active'] = key
        self.save(data)


def resolve_profile(root):
    store = ProfileStore(root)
    data = store.read()
    key = os.environ.get('QQPET_PROFILE_ID', data['active'])
    if key not in data['profiles']:
        raise ValueError('启动配置不存在')
    return key, store.directory(key)
